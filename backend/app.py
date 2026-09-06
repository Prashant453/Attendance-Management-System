from flask import Flask, request, jsonify
from flask_cors import CORS
import pymysql
import bcrypt
import jwt
import datetime
import os
import json
import logging
import uuid
import numpy as np
from functools import wraps
from database import get_db_connection, init_db
from face_utils import get_face_encodings, compare_faces, recognize_multi_faces, detect_multi_faces, check_liveness
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


from flask import send_from_directory

frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
app = Flask(__name__, static_folder=frontend_dir if os.path.exists(frontend_dir) else None)

# Enable universal Cross-Origin Resource Sharing (CORS) for Vercel/Cloud frontends
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'attendify_super_jwt_secret_key_2026')

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,x-access-token,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    return response

# Helper to run dictionary queries in TiDB Cloud with connection reuse
def query_db(query, args=(), one=False):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(query, args)
        rv = cur.fetchall()
        return (rv[0] if rv else None) if one else rv


# JWT Authentication Decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'x-access-token' in request.headers:
            token = request.headers['x-access-token']
        if not token:
            return jsonify({'message': 'Authentication token is missing!'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = data
        except Exception:
            return jsonify({'message': 'Authentication token is invalid or expired!'}), 401
        return f(current_user, *args, **kwargs)
    return decorated


@app.route('/')
def index():
    if os.path.exists(os.path.join(frontend_dir, 'index.html')):
        return send_from_directory(frontend_dir, 'index.html')
    return jsonify({
        'status': 'online',
        'service': 'Attendify Facial Biometrics Backend API',
        'version': '2.0.0',
        'database': 'TiDB Cloud Serverless',
        'health_check': '/api/health'
    }), 200

@app.route('/health')
@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok;")
            res = cur.fetchone()
        conn.close()
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'db_type': 'TiDB Cloud Serverless',
            'timestamp': datetime.datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            'status': 'degraded',
            'database_error': str(e),
            'timestamp': datetime.datetime.utcnow().isoformat()
        }), 500

@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(os.path.join(frontend_dir, path)):
        return send_from_directory(frontend_dir, path)
    if os.path.exists(os.path.join(frontend_dir, 'index.html')):
        return send_from_directory(frontend_dir, 'index.html')
    return jsonify({'message': 'Endpoint not found'}), 404


# ----------------- Auth Endpoints -----------------

@app.route('/api/login', methods=['POST'])
def login():
    auth = request.json
    if not auth or not auth.get('username') or not auth.get('password'):
        return jsonify({'message': 'Missing credentials'}), 400
    
    try:
        user = query_db("SELECT * FROM users WHERE username = %s", (auth['username'],), one=True)
        
        if user and bcrypt.checkpw(auth['password'].encode('utf8'), user['password'].encode('utf8')):
            if user.get('is_active') == 0:
                return jsonify({'message': 'This account has been deactivated by administration. Access denied.'}), 403
                
            token = jwt.encode({
                'user_id': user['id'],
                'username': user['username'],
                'role': user['role'],
                'full_name': user['full_name'],
                'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
            }, app.config['SECRET_KEY'], algorithm="HS256")
            
            return jsonify({
                'token': token,
                'role': user['role'],
                'full_name': user['full_name'],
                'user_id': user['id'],
                'email': user['email'],
                'username': user['username']
            })
        
        return jsonify({'message': 'Invalid username or password'}), 401
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'message': f'Server error: {str(e)}'}), 500

@app.route('/api/profile', methods=['GET'])
@token_required
def get_profile(current_user):
    user = query_db("SELECT id, username, email, role, full_name, is_active, created_at FROM users WHERE id = %s", (current_user['user_id'],), one=True)
    if not user:
        return jsonify({'message': 'User not found'}), 404
    
    if user['role'] == 'student':
        std = query_db("""
            SELECT s.id as student_id, s.course_id, c.course_name 
            FROM students s 
            LEFT JOIN courses c ON s.course_id = c.id 
            WHERE s.user_id = %s
        """, (user['id'],), one=True)
        if std:
            user['student_id'] = std['student_id']
            user['course_id'] = std['course_id']
            user['course_name'] = std['course_name']
            
    return jsonify(user)


# ----------------- Comprehensive Student Management -----------------

@app.route('/api/students', methods=['GET'])
@token_required
def get_students(current_user):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    course_id = request.args.get('course_id')
    status = request.args.get('status') # 'active', 'inactive'
    
    query = """
        SELECT s.id, s.user_id, u.username, u.email, u.full_name, c.course_name, s.course_id, 
               COALESCE(s.is_active, 1) as is_active, u.created_at,
               (s.face_encoding IS NOT NULL AND s.face_encoding != '') as face_registered
        FROM students s
        JOIN users u ON s.user_id = u.id
        JOIN courses c ON s.course_id = c.id
        WHERE 1=1
    """
    params = []
    if course_id:
        query += " AND s.course_id = %s"
        params.append(course_id)
    if status == 'active':
        query += " AND COALESCE(s.is_active, 1) = 1"
    elif status == 'inactive':
        query += " AND COALESCE(s.is_active, 1) = 0"
        
    query += " ORDER BY s.id ASC"
    students = query_db(query, tuple(params))
    for s in students:
        s['face_registered'] = bool(s['face_registered'])
        s['is_active'] = bool(s['is_active'])
    return jsonify(students)

@app.route('/api/students/<int:student_id>', methods=['GET'])
@token_required
def get_student_detail(current_user, student_id):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403

    query = """
        SELECT s.id, s.user_id, u.username, u.email, u.full_name, c.course_name, s.course_id, 
               COALESCE(s.is_active, 1) as is_active, u.created_at,
               (s.face_encoding IS NOT NULL AND s.face_encoding != '') as face_registered
        FROM students s
        JOIN users u ON s.user_id = u.id
        JOIN courses c ON s.course_id = c.id
        WHERE s.id = %s
    """
    student = query_db(query, (student_id,), one=True)
    if not student:
        return jsonify({'message': 'Student not found'}), 404

    student['face_registered'] = bool(student['face_registered'])
    student['is_active'] = bool(student['is_active'])

    # Aggregate attendance statistics for this student
    att_stats = query_db("""
        SELECT status, COUNT(*) as count 
        FROM attendance 
        WHERE student_id = %s 
        GROUP BY status
    """, (student_id,))

    present_count = sum(r['count'] for r in att_stats if r['status'] == 'Present')
    total_classes = sum(r['count'] for r in att_stats)
    rate = round((present_count / total_classes * 100), 1) if total_classes > 0 else 0.0

    last_session = query_db("""
        SELECT a.date, a.status, sub.subject_name 
        FROM attendance a
        JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.student_id = %s
        ORDER BY a.date DESC, a.id DESC LIMIT 1
    """, (student_id,), one=True)

    student['stats'] = {
        'total_classes': total_classes,
        'present_count': present_count,
        'absent_count': total_classes - present_count,
        'attendance_percentage': rate,
        'last_session': last_session
    }

    return jsonify(student)

@app.route('/api/students', methods=['POST'])
@token_required
def register_student(current_user):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized. Admin privilege required.'}), 403
    
    data = request.json
    if not data or not data.get('username') or not data.get('password') or not data.get('course_id'):
        return jsonify({'message': 'Missing required student fields (username, password, course_id)'}), 400

    hashed_pw = bcrypt.hashpw(data['password'].encode('utf8'), bcrypt.gensalt()).decode('utf8')
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password, email, role, full_name, is_active) VALUES (%s, %s, %s, %s, %s, 1)",
                (data['username'], hashed_pw, data.get('email', f"{data['username']}@student.edu"), 'student', data.get('full_name', data['username']))
            )
            user_id = cursor.lastrowid
            cursor.execute("INSERT INTO students (user_id, course_id, is_active) VALUES (%s, %s, 1)", (user_id, data['course_id']))
            student_id = cursor.lastrowid
        conn.commit()
        return jsonify({'message': 'Student created successfully', 'id': student_id}), 201
    except pymysql.err.IntegrityError as err:
        return jsonify({'message': f'Student username or email already exists ({err.args[1]})'}), 400
    except Exception as e:
        return jsonify({'message': str(e)}), 400
    finally:
        conn.close()

@app.route('/api/students/<int:student_id>', methods=['PUT'])
@token_required
def update_student(current_user, student_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized. Admin privilege required.'}), 403

    data = request.json
    if not data:
        return jsonify({'message': 'Update payload required'}), 400

    student = query_db("SELECT id, user_id FROM students WHERE id = %s", (student_id,), one=True)
    if not student:
        return jsonify({'message': 'Student not found'}), 404

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Update users table
            user_updates = []
            user_params = []
            if 'full_name' in data:
                user_updates.append("full_name = %s")
                user_params.append(data['full_name'])
            if 'email' in data:
                user_updates.append("email = %s")
                user_params.append(data['email'])
            if 'username' in data:
                user_updates.append("username = %s")
                user_params.append(data['username'])
            if 'password' in data and data['password']:
                hashed_pw = bcrypt.hashpw(data['password'].encode('utf8'), bcrypt.gensalt()).decode('utf8')
                user_updates.append("password = %s")
                user_params.append(hashed_pw)

            if user_updates:
                user_params.append(student['user_id'])
                cursor.execute(f"UPDATE users SET {', '.join(user_updates)} WHERE id = %s", tuple(user_params))

            # Update students table
            if 'course_id' in data:
                cursor.execute("UPDATE students SET course_id = %s WHERE id = %s", (data['course_id'], student_id))

        conn.commit()
        return jsonify({'message': 'Student details updated successfully', 'success': True})
    except pymysql.err.IntegrityError as err:
        return jsonify({'message': f'Conflict: Username or email already in use ({err.args[1]})'}), 400
    except Exception as e:
        return jsonify({'message': f'Update error: {str(e)}'}), 500
    finally:
        conn.close()

@app.route('/api/students/<int:student_id>/status', methods=['PATCH'])
@token_required
def toggle_student_status(current_user, student_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized. Admin privilege required.'}), 403

    data = request.json
    if data is None or 'is_active' not in data:
        return jsonify({'message': 'is_active flag (0 or 1) required'}), 400

    new_status = 1 if data['is_active'] in (1, True, '1', 'true') else 0
    student = query_db("SELECT id, user_id FROM students WHERE id = %s", (student_id,), one=True)
    if not student:
        return jsonify({'message': 'Student not found'}), 404

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE students SET is_active = %s WHERE id = %s", (new_status, student_id))
            cursor.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, student_id))
        conn.commit()
        status_text = "activated" if new_status == 1 else "deactivated"
        return jsonify({
            'message': f'Student account has been {status_text}. All historical records remain safe.',
            'is_active': bool(new_status),
            'success': True
        })
    except Exception as e:
        return jsonify({'message': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/students/<int:student_id>/face', methods=['DELETE'])
@token_required
def delete_student_face(current_user, student_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized. Admin privilege required.'}), 403

    student = query_db("SELECT id FROM students WHERE id = %s", (student_id,), one=True)
    if not student:
        return jsonify({'message': 'Student not found'}), 404

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE students SET face_encoding = NULL WHERE id = %s", (student_id,))
        conn.commit()
        return jsonify({'message': 'Facial biometric data removed. Ready for re-enrollment.', 'success': True})
    except Exception as e:
        return jsonify({'message': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/students/<int:student_id>/attendance', methods=['GET'])
@token_required
def get_student_attendance_history(current_user, student_id):
    if current_user['role'] not in ['admin', 'teacher']:
        # If student, ensure they only view their own records
        if current_user['role'] == 'student':
            res = query_db("SELECT id FROM students WHERE user_id = %s", (current_user['user_id'],), one=True)
            if not res or res['id'] != student_id:
                return jsonify({'message': 'Unauthorized to view other student records'}), 403

    query = """
        SELECT a.id, a.date, a.status, a.method, a.created_at, sub.subject_name, c.course_name,
               u_teach.full_name as teacher_name
        FROM attendance a
        JOIN subjects sub ON a.subject_id = sub.id
        JOIN courses c ON sub.course_id = c.id
        LEFT JOIN users u_teach ON a.teacher_id = u_teach.id
        WHERE a.student_id = %s
        ORDER BY a.date DESC, a.id DESC
    """
    records = query_db(query, (student_id,))
    return jsonify(records)

# ----------------- Faculty / Teacher Management -----------------


@app.route('/api/teachers', methods=['GET', 'POST'])
@token_required
def manage_teachers(current_user):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized'}), 403
    
    if request.method == 'POST':
        data = request.json
        hashed_pw = bcrypt.hashpw(data['password'].encode('utf8'), bcrypt.gensalt()).decode('utf8')
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (username, password, email, role, full_name) VALUES (%s, %s, %s, %s, %s)",
                    (data['username'], hashed_pw, data['email'], 'teacher', data['full_name'])
                )
                res_id = cursor.lastrowid
            return jsonify({'message': 'Teacher account created successfully', 'id': res_id}), 201
        except Exception as e:
            return jsonify({'message': str(e)}), 400
        finally:
            conn.close()

    teachers = query_db("SELECT id, username, email, full_name, created_at FROM users WHERE role = 'teacher' ORDER BY id ASC")
    return jsonify(teachers)

# ----------------- Face Data Registration -----------------

@app.route('/api/students/<int:student_id>/register-face', methods=['POST'])
@token_required
def register_face(current_user, student_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized. Admin privilege required.'}), 403
    
    data = request.json
    if not data or not data.get('image'):
        return jsonify({'message': 'Image payload is required for face registration.'}), 400

    student = query_db("SELECT id FROM students WHERE id = %s", (student_id,), one=True)
    if not student:
        return jsonify({'message': f'Student with ID {student_id} not found.'}), 404

    encoding = get_face_encodings(data['image'])
    if not encoding or len(encoding) != 128:
        return jsonify({
            'message': 'No clear frontal face detected in the image. Please center the student face inside the frame with adequate lighting.',
            'success': False
        }), 400
    
    encoding_json = json.dumps(encoding)
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE students SET face_encoding = %s WHERE id = %s", (encoding_json, student_id))
        conn.commit()
        logger.info(f"Successfully registered 128-d face biometric for student ID #{student_id} in TiDB Cloud.")
        return jsonify({
            'message': 'Face biometric registered successfully and stored in TiDB Cloud.',
            'success': True,
            'student_id': student_id
        })
    except Exception as e:
        logger.error(f"Database error saving face encoding: {e}")
        return jsonify({'message': f'Database error: {str(e)}'}), 500
    finally:
        conn.close()

# ----------------- Attendance Logic -----------------

@app.route('/api/attendance/manual', methods=['POST'])
@token_required
def mark_attendance_manual(current_user):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    data = request.json
    if not data or not data.get('student_id') or not data.get('subject_id') or not data.get('date'):
        return jsonify({'message': 'Missing required fields: student_id, subject_id, date'}), 400

    # Strict Program/Course-Based Attendance Enforcement
    enrollment = query_db("""
        SELECT s.id as student_id, s.course_id as student_course_id,
               sub.id as subject_id, sub.course_id as subject_course_id,
               c_std.course_name as student_program, c_sub.course_name as subject_program
        FROM students s
        JOIN courses c_std ON s.course_id = c_std.id
        JOIN subjects sub ON sub.id = %s
        JOIN courses c_sub ON sub.course_id = c_sub.id
        WHERE s.id = %s
    """, (data['subject_id'], data['student_id']), one=True)

    if not enrollment:
        return jsonify({'message': 'Invalid student or subject ID.'}), 404

    if enrollment['student_course_id'] != enrollment['subject_course_id']:
        return jsonify({
            'message': f"Academic Program Mismatch: Student is enrolled in '{enrollment['student_program']}', but this subject is offered under '{enrollment['subject_program']}'. Cross-program attendance is not permitted.",
            'error_code': 'PROGRAM_MISMATCH'
        }), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM attendance WHERE student_id = %s AND subject_id = %s AND date = %s",
                (data['student_id'], data['subject_id'], data['date'])
            )
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute(
                    "UPDATE attendance SET status = %s, teacher_id = %s, method = 'Manual' WHERE id = %s",
                    (data.get('status', 'Present'), current_user['user_id'], existing['id'])
                )
            else:
                cursor.execute(
                    "INSERT INTO attendance (student_id, subject_id, teacher_id, date, status, method) VALUES (%s, %s, %s, %s, %s, %s)",
                    (data['student_id'], data['subject_id'], current_user['user_id'], data['date'], data.get('status', 'Present'), 'Manual')
                )
        conn.commit()
        return jsonify({'message': 'Attendance marked successfully', 'status': data.get('status', 'Present')})
    except Exception as e:
        return jsonify({'message': str(e)}), 500


def calculate_student_attendance_metrics(attendance_records, required_pct=75.0):
    """
    Computes rigorous AI Attendance Intelligence metrics:
    - Total, Attended, Absent, Rate %
    - Temporal Trend (IMPROVING ↑, STABLE →, DECLINING ↓)
    - Predictive Risk Level (LOW 🟢, MEDIUM 🟡, HIGH 🔴)
    - Mathematical Miss / Recovery Forecast
    """
    total = len(attendance_records)
    attended = sum(1 for r in attendance_records if r['status'] == 'Present')
    absent = total - attended
    
    pct = round((attended / total * 100.0), 1) if total > 0 else 100.0
    
    # 1. Temporal Trend Calculation (Chronological comparison)
    sorted_records = sorted(attendance_records, key=lambda x: (str(x.get('date', '')), x.get('id', 0)))
    
    trend = 'STABLE'
    trend_symbol = '→'
    
    if total >= 4:
        w_size = min(5, total // 2)
        recent_window = sorted_records[-w_size:]
        prior_window = sorted_records[-2 * w_size : -w_size]
        
        recent_rate = (sum(1 for r in recent_window if r['status'] == 'Present') / len(recent_window)) * 100.0
        prior_rate = (sum(1 for r in prior_window if r['status'] == 'Present') / len(prior_window)) * 100.0
        
        diff = recent_rate - prior_rate
        if diff >= 5.0 or (all(r['status'] == 'Present' for r in recent_window[-3:]) and pct >= 80.0):
            trend = 'IMPROVING'
            trend_symbol = '↑'
        elif diff <= -5.0 or (sum(1 for r in recent_window[-3:] if r['status'] == 'Absent') >= 2 and pct < 85.0):
            trend = 'DECLINING'
            trend_symbol = '↓'
    elif total > 0:
        last_status = sorted_records[-1]['status']
        if last_status == 'Present' and pct >= 75.0:
            trend = 'IMPROVING'
            trend_symbol = '↑'
        elif last_status == 'Absent' or pct < 75.0:
            trend = 'DECLINING'
            trend_symbol = '↓'
            
    # 2. Predictive Risk Engine
    if total == 0:
        risk_level = 'LOW'
        risk_label = 'LOW'
    elif pct < 65.0:
        risk_level = 'HIGH'
        risk_label = 'HIGH'
    elif pct < required_pct:
        if trend == 'DECLINING':
            risk_level = 'HIGH'
            risk_label = 'HIGH'
        else:
            risk_level = 'MEDIUM'
            risk_label = 'MEDIUM'
    elif pct < 80.0 and trend == 'DECLINING':
        risk_level = 'MEDIUM'
        risk_label = 'MEDIUM'
    else:
        risk_level = 'LOW'
        risk_label = 'LOW'

    # 3. Actionable Attendance Calculations
    if total == 0:
        can_miss = 0
        needed_to_recover = 0
        actionable_advice = "No classes conducted yet. Maintain consistent attendance."
    elif pct >= required_pct:
        can_miss = max(0, int((attended - (required_pct / 100.0) * total) / (required_pct / 100.0)))
        needed_to_recover = 0
        if can_miss > 0:
            actionable_advice = f"You can miss approximately {can_miss} more class{'es' if can_miss > 1 else ''} while remaining above {int(required_pct)}%."
        else:
            actionable_advice = f"Your attendance is near the {int(required_pct)}% threshold. Do not miss any upcoming classes."
    else:
        needed_to_recover = max(1, int(np.ceil(((required_pct / 100.0) * total - attended) / (1.0 - (required_pct / 100.0)))))
        can_miss = 0
        actionable_advice = f"You need to attend approximately {needed_to_recover} consecutive class{'es' if needed_to_recover > 1 else ''} to reach {int(required_pct)}%."

    return {
        'total_classes': total,
        'attended_classes': attended,
        'absent_classes': absent,
        'attendance_rate': pct,
        'required_rate': required_pct,
        'trend': trend,
        'trend_symbol': trend_symbol,
        'risk_level': risk_level,
        'risk_label': risk_label,
        'can_miss': can_miss,
        'needed_to_recover': needed_to_recover,
        'actionable_advice': actionable_advice
    }


# ----------------- Attendance Session Management -----------------

@app.route('/api/sessions/start', methods=['POST'])
@token_required
def start_attendance_session(current_user):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    data = request.json or {}
    subject_id = data.get('subject_id')
    date_str = data.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    
    if not subject_id:
        return jsonify({'message': 'subject_id is required to start a session'}), 400

    subject = query_db("SELECT s.*, c.course_name FROM subjects s JOIN courses c ON s.course_id = c.id WHERE s.id = %s", (subject_id,), one=True)
    if not subject:
        return jsonify({'message': 'Subject not found'}), 404

    # Check for existing active or paused session
    existing_active = query_db(
        "SELECT * FROM attendance_sessions WHERE subject_id = %s AND date = %s AND status IN ('ACTIVE', 'PAUSED') ORDER BY id DESC LIMIT 1",
        (subject_id, date_str), one=True
    )
    if existing_active:
        return jsonify({
            'message': 'Resumed existing active attendance session.',
            'session': existing_active,
            'subject': subject
        }), 200

    session_code = f"SESS-{date_str.replace('-', '')}-{subject_id}-{uuid.uuid4().hex[:6].upper()}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO attendance_sessions (subject_id, teacher_id, session_code, date, status, total_marked)
                VALUES (%s, %s, %s, %s, 'ACTIVE', 0)
            """, (subject_id, current_user['user_id'], session_code, date_str))
            sess_id = cur.lastrowid
        conn.commit()
        
        new_session = query_db("SELECT * FROM attendance_sessions WHERE id = %s", (sess_id,), one=True)
        return jsonify({
            'message': 'Attendance session started successfully.',
            'session': new_session,
            'subject': subject
        }), 201
    except Exception as e:
        logger.error(f"Failed to start attendance session: {e}")
        return jsonify({'message': str(e)}), 500


@app.route('/api/sessions/<int:session_id>/pause', methods=['POST'])
@token_required
def pause_attendance_session(current_user, session_id):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    session = query_db("SELECT * FROM attendance_sessions WHERE id = %s", (session_id,), one=True)
    if not session:
        return jsonify({'message': 'Session not found'}), 404

    new_status = 'ACTIVE' if session['status'] == 'PAUSED' else 'PAUSED'
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE attendance_sessions SET status = %s WHERE id = %s", (new_status, session_id))
        conn.commit()
        return jsonify({'message': f'Session status updated to {new_status}', 'status': new_status, 'session_id': session_id})
    except Exception as e:
        return jsonify({'message': str(e)}), 500


@app.route('/api/sessions/<int:session_id>/end', methods=['POST'])
@token_required
def end_attendance_session(current_user, session_id):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Count marked attendance records for this session
            cur.execute("SELECT COUNT(*) as cnt FROM attendance WHERE session_id = %s AND status = 'Present'", (session_id,))
            marked_cnt = cur.fetchone()['cnt']
            
            cur.execute("""
                UPDATE attendance_sessions 
                SET status = 'ENDED', end_time = CURRENT_TIMESTAMP, total_marked = %s 
                WHERE id = %s
            """, (marked_cnt, session_id))
        conn.commit()
        
        final_session = query_db("SELECT * FROM attendance_sessions WHERE id = %s", (session_id,), one=True)
        return jsonify({
            'message': 'Attendance session finalized and closed.',
            'session': final_session,
            'total_marked': marked_cnt
        })
    except Exception as e:
        return jsonify({'message': str(e)}), 500


@app.route('/api/sessions/active', methods=['GET'])
@token_required
def get_active_session(current_user):
    subject_id = request.args.get('subject_id')
    date_str = request.args.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    
    query = """
        SELECT sess.*, sub.subject_name, c.course_name, u.full_name as teacher_name
        FROM attendance_sessions sess
        JOIN subjects sub ON sess.subject_id = sub.id
        JOIN courses c ON sub.course_id = c.id
        JOIN users u ON sess.teacher_id = u.id
        WHERE sess.status IN ('ACTIVE', 'PAUSED')
    """
    params = []
    if subject_id:
        query += " AND sess.subject_id = %s"
        params.append(subject_id)
    if date_str:
        query += " AND sess.date = %s"
        params.append(date_str)
        
    query += " ORDER BY sess.id DESC LIMIT 1"
    active_sess = query_db(query, tuple(params), one=True)
    return jsonify({'active_session': active_sess})


@app.route('/api/sessions', methods=['GET'])
@token_required
def list_attendance_sessions(current_user):
    subject_id = request.args.get('subject_id')
    query = """
        SELECT sess.*, sub.subject_name, c.course_name, u.full_name as teacher_name,
               (SELECT COUNT(*) FROM attendance a WHERE a.session_id = sess.id AND a.status = 'Present') as marked_count
        FROM attendance_sessions sess
        JOIN subjects sub ON sess.subject_id = sub.id
        JOIN courses c ON sub.course_id = c.id
        JOIN users u ON sess.teacher_id = u.id
        WHERE 1=1
    """
    params = []
    if subject_id:
        query += " AND sess.subject_id = %s"
        params.append(subject_id)
    query += " ORDER BY sess.id DESC LIMIT 50"
    return jsonify(query_db(query, tuple(params)))


# ----------------- Smart Multi-Face Detection & Liveness Attendance -----------------

@app.route('/api/attendance/smart-face-scan', methods=['POST'])
@token_required
def smart_multi_face_scan(current_user):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    data = request.json
    if not data or not data.get('subject_id') or not data.get('image'):
        return jsonify({'message': 'Missing required fields: subject_id and image'}), 400

    subject_id = data['subject_id']
    target_date = data.get('date', datetime.date.today().strftime('%Y-%m-%d'))
    session_id = data.get('session_id')

    # 1. Subject & Program Alignment Verification
    subject = query_db("SELECT id, subject_name, course_id FROM subjects WHERE id = %s", (subject_id,), one=True)
    if not subject:
        return jsonify({'message': 'Subject not found'}), 404

    # 2. Check Session status if provided
    if session_id:
        sess_info = query_db("SELECT * FROM attendance_sessions WHERE id = %s", (session_id,), one=True)
        if sess_info and sess_info['status'] == 'PAUSED':
            return jsonify({
                'message': 'Attendance session is currently paused. Please resume session to record attendance.',
                'error_code': 'SESSION_PAUSED',
                'faces': []
            }), 400
        elif sess_info and sess_info['status'] == 'ENDED':
            return jsonify({
                'message': 'Attendance session has ended. Please start a new session.',
                'error_code': 'SESSION_ENDED',
                'faces': []
            }), 400

    # 3. Retrieve enrolled students with registered biometric encodings
    known_students = query_db("""
        SELECT s.id, s.course_id, s.face_encoding, u.full_name, u.username, u.email
        FROM students s
        JOIN users u ON s.user_id = u.id
        WHERE s.course_id = %s AND COALESCE(s.is_active, 1) = 1 AND s.face_encoding IS NOT NULL AND s.face_encoding != ''
    """, (subject['course_id'],))

    known_encodings = {}
    student_map = {}
    for ks in known_students:
        student_map[ks['id']] = ks
        try:
            parsed = json.loads(ks['face_encoding'])
            if isinstance(parsed, list) and len(parsed) == 128:
                known_encodings[ks['id']] = parsed
        except Exception:
            continue

    if not known_encodings:
        return jsonify({
            'message': f"No enrolled students with registered facial biometrics found for '{subject['subject_name']}'. Please enroll student faces in Student Management.",
            'error_code': 'NO_ENROLLED_BIOMETRICS',
            'faces': [],
            'detected_count': 0
        }), 400

    # 4. Multi-Face Detection + Liveness + Recognition Pipeline
    detected_faces = recognize_multi_faces(known_encodings, data['image'], tolerance=0.55)
    
    conn = get_db_connection()
    newly_marked_students = []
    
    try:
        with conn.cursor() as cur:
            for face in detected_faces:
                sid = face.get('student_id')
                is_live = face.get('liveness_passed', False)
                
                # Log Liveness Attempt
                cur.execute("""
                    INSERT INTO liveness_attempts (session_id, student_id, subject_id, liveness_status, liveness_type, score, details)
                    VALUES (%s, %s, %s, %s, 'TEXTURE_AND_CHROMINANCE', %s, %s)
                """, (
                    session_id, sid, subject_id,
                    'PASSED' if is_live else 'FAILED',
                    face.get('liveness_score', 1.0),
                    face.get('liveness_message', '')
                ))

                # If liveness failed on a recognized face, record anomaly
                if sid and not is_live:
                    st_name = student_map.get(sid, {}).get('full_name', f'Student #{sid}')
                    cur.execute("""
                        INSERT INTO suspicious_activities (student_id, student_name, activity_type, severity, details)
                        VALUES (%s, %s, 'REPEATED_LIVENESS_FAILURE', 'MEDIUM', %s)
                    """, (sid, st_name, f"Liveness check failed ({face.get('liveness_message')}) during scan"))

                if sid and is_live:
                    st_obj = student_map.get(sid)
                    face['student_name'] = st_obj['full_name']
                    face['student_username'] = st_obj['username']
                    
                    # Check if already marked for this subject and date
                    cur.execute(
                        "SELECT id, status, session_id FROM attendance WHERE student_id = %s AND subject_id = %s AND date = %s",
                        (sid, subject_id, target_date)
                    )
                    existing = cur.fetchone()

                    if not existing:
                        cur.execute("""
                            INSERT INTO attendance (session_id, student_id, subject_id, teacher_id, date, status, method)
                            VALUES (%s, %s, %s, %s, %s, 'Present', 'AI-MultiFace')
                        """, (session_id, sid, subject_id, current_user['user_id'], target_date))
                        
                        face['status'] = 'RECOGNIZED'
                        face['attendance_marked'] = True
                        newly_marked_students.append({
                            'id': sid,
                            'full_name': st_obj['full_name'],
                            'username': st_obj['username'],
                            'confidence': face['confidence'],
                            'timestamp': datetime.datetime.now().strftime('%H:%M:%S')
                        })
                    elif existing['status'] == 'Present':
                        face['status'] = 'RECOGNIZED'
                        face['attendance_marked'] = False
                        face['already_marked'] = True
                    else:
                        cur.execute("""
                            UPDATE attendance SET status = 'Present', method = 'AI-MultiFace', session_id = %s, teacher_id = %s WHERE id = %s
                        """, (session_id, current_user['user_id'], existing['id']))
                        face['status'] = 'RECOGNIZED'
                        face['attendance_marked'] = True
                        newly_marked_students.append({
                            'id': sid,
                            'full_name': st_obj['full_name'],
                            'username': st_obj['username'],
                            'confidence': face['confidence'],
                            'timestamp': datetime.datetime.now().strftime('%H:%M:%S')
                        })
                elif sid and not is_live:
                    st_obj = student_map.get(sid)
                    face['student_name'] = st_obj['full_name']
                    face['status'] = 'LIVENESS_FAILED'
                else:
                    face['student_name'] = 'Unknown Face'
                    face['status'] = 'UNKNOWN'

            # Update session total_marked
            if session_id:
                cur.execute("SELECT COUNT(*) as cnt FROM attendance WHERE session_id = %s AND status = 'Present'", (session_id,))
                total_in_session = cur.fetchone()['cnt']
                cur.execute("UPDATE attendance_sessions SET total_marked = %s WHERE id = %s", (total_in_session, session_id))

        conn.commit()

        recognized_cnt = sum(1 for f in detected_faces if f['status'] == 'RECOGNIZED')
        unknown_cnt = sum(1 for f in detected_faces if f['status'] == 'UNKNOWN')
        liveness_failed_cnt = sum(1 for f in detected_faces if f['status'] == 'LIVENESS_FAILED')

        return jsonify({
            'success': True,
            'detected_count': len(detected_faces),
            'recognized_count': recognized_cnt,
            'unknown_count': unknown_cnt,
            'liveness_failed_count': liveness_failed_cnt,
            'newly_marked_count': len(newly_marked_students),
            'newly_marked_students': newly_marked_students,
            'faces': detected_faces,
            'session_id': session_id
        })
    except Exception as e:
        logger.error(f"Error in smart multi-face scan: {e}")
        return jsonify({'message': str(e)}), 500


# ----------------- AI Attendance Intelligence & Predictive Analytics -----------------

@app.route('/api/analytics/intelligence', methods=['GET'])
@token_required
def get_attendance_intelligence(current_user):
    try:
        course_id = request.args.get('course_id')
        subject_id = request.args.get('subject_id')

        # 1. Fetch Students
        std_query = """
            SELECT s.id, s.user_id, s.course_id, s.is_active, u.full_name, u.email, u.username, c.course_name
            FROM students s
            JOIN users u ON s.user_id = u.id
            JOIN courses c ON s.course_id = c.id
            WHERE COALESCE(s.is_active, 1) = 1
        """
        std_params = []
        if course_id:
            std_query += " AND s.course_id = %s"
            std_params.append(course_id)
        students = query_db(std_query, tuple(std_params))

        # 2. Fetch all attendance records
        att_query = "SELECT id, student_id, subject_id, date, status, method FROM attendance WHERE 1=1"
        att_params = []
        if subject_id:
            att_query += " AND subject_id = %s"
            att_params.append(subject_id)
        all_att = query_db(att_query, tuple(att_params))

        # Group attendance by student
        records_by_student = {}
        for row in all_att:
            sid = row['student_id']
            if sid not in records_by_student:
                records_by_student[sid] = []
            records_by_student[sid].append(row)

        students_intelligence = []
        at_risk_students = []

        total_students = len(students)
        total_present_today = 0
        total_absent_today = 0
        today_str = datetime.date.today().strftime('%Y-%m-%d')

        for st in students:
            st_records = records_by_student.get(st['id'], [])
            metrics = calculate_student_attendance_metrics(st_records, required_pct=75.0)
            
            # Today's status
            today_record = next((r for r in st_records if r['date'] == today_str), None)
            today_status = today_record['status'] if today_record else 'Not Marked'
            if today_status == 'Present':
                total_present_today += 1
            elif today_status == 'Absent':
                total_absent_today += 1

            st_intel = {
                'id': st['id'],
                'full_name': st['full_name'],
                'email': st['email'],
                'username': st['username'],
                'course_name': st['course_name'],
                'today_status': today_status,
                **metrics
            }
            students_intelligence.append(st_intel)

            if metrics['risk_level'] in ['HIGH', 'MEDIUM']:
                at_risk_students.append(st_intel)

        # Sort at-risk students by lowest attendance percentage first
        at_risk_students.sort(key=lambda x: (x['attendance_rate'], x['total_classes']))

        avg_attendance_rate = round(np.mean([s['attendance_rate'] for s in students_intelligence]), 1) if students_intelligence else 0.0
        below_req_count = sum(1 for s in students_intelligence if s['attendance_rate'] < 75.0)
        high_risk_count = sum(1 for s in students_intelligence if s['risk_level'] == 'HIGH')

        # Timeline trends (Daily for last 14 days)
        daily_trends = query_db("""
            SELECT date,
                   SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) as present_count,
                   SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) as absent_count,
                   COUNT(*) as total_count
            FROM attendance
            GROUP BY date
            ORDER BY date DESC
            LIMIT 14
        """)

        # Suspicious activities count
        susp_count = query_db("SELECT COUNT(*) as cnt FROM suspicious_activities WHERE COALESCE(resolved, 0) = 0", one=True)['cnt']

        return jsonify({
            'summary': {
                'total_students': total_students,
                'present_today': total_present_today,
                'absent_today': total_absent_today,
                'avg_attendance_rate': avg_attendance_rate,
                'below_required_count': below_req_count,
                'high_risk_count': high_risk_count,
                'unresolved_anomalies': susp_count
            },
            'students_intelligence': students_intelligence,
            'at_risk_students': at_risk_students,
            'daily_timeline': daily_trends[::-1]
        })
    except Exception as e:
        logger.error(f"Intelligence analytics calculation error: {e}")
        return jsonify({'message': str(e)}), 500


@app.route('/api/analytics/student/<int:student_id>', methods=['GET'])
@token_required
def get_student_analytics(current_user, student_id):
    # Authorization: Student can only access their own analytics
    if current_user['role'] == 'student':
        logged_std = query_db("SELECT id FROM students WHERE user_id = %s", (current_user['user_id'],), one=True)
        if not logged_std or logged_std['id'] != student_id:
            return jsonify({'message': 'Access denied: you can only view your own student analytics.'}), 403

    student = query_db("""
        SELECT s.id, s.user_id, s.course_id, u.full_name, u.email, u.username, c.course_name
        FROM students s
        JOIN users u ON s.user_id = u.id
        JOIN courses c ON s.course_id = c.id
        WHERE s.id = %s
    """, (student_id,), one=True)

    if not student:
        return jsonify({'message': 'Student not found'}), 404

    # All attendance records for this student
    all_records = query_db("""
        SELECT a.id, a.date, a.status, a.method, a.created_at, sub.id as subject_id, sub.subject_name
        FROM attendance a
        JOIN subjects sub ON a.subject_id = sub.id
        WHERE a.student_id = %s
        ORDER BY a.date DESC, a.id DESC
    """, (student_id,))

    overall_metrics = calculate_student_attendance_metrics(all_records, required_pct=75.0)

    # Subject-wise Breakdown
    records_by_sub = {}
    for r in all_records:
        sub_name = r['subject_name']
        if sub_name not in records_by_sub:
            records_by_sub[sub_name] = []
        records_by_sub[sub_name].append(r)

    subject_breakdown = []
    for sub_name, sub_recs in records_by_sub.items():
        sub_metrics = calculate_student_attendance_metrics(sub_recs, required_pct=75.0)
        subject_breakdown.append({
            'subject_name': sub_name,
            **sub_metrics
        })

    return jsonify({
        'student': student,
        'overall_metrics': overall_metrics,
        'subject_breakdown': subject_breakdown,
        'recent_history': all_records[:15]
    })


# ----------------- Suspicious Activity & Anomaly Detection -----------------

@app.route('/api/audit/suspicious-activity', methods=['GET'])
@token_required
def get_suspicious_activities(current_user):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    activities = query_db("""
        SELECT id, student_id, student_name, activity_type, severity, details, resolved, timestamp
        FROM suspicious_activities
        ORDER BY id DESC
        LIMIT 50
    """)
    return jsonify(activities)


@app.route('/api/audit/suspicious-activity/<int:activity_id>/resolve', methods=['POST'])
@token_required
def resolve_suspicious_activity(current_user, activity_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized'}), 403
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE suspicious_activities SET resolved = 1 WHERE id = %s", (activity_id,))
        conn.commit()
        return jsonify({'message': 'Suspicious incident marked as resolved.', 'success': True})
    except Exception as e:
        return jsonify({'message': str(e)}), 500


# ----------------- Advanced Filtered Attendance History -----------------

@app.route('/api/reports/attendance-advanced', methods=['GET'])
@token_required
def get_advanced_attendance_report(current_user):
    search = request.args.get('search', '').strip()
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    subject_id = request.args.get('subject_id')
    course_id = request.args.get('course_id')
    student_id = request.args.get('student_id')
    status = request.args.get('status')
    method = request.args.get('method')
    session_id = request.args.get('session_id')
    page = max(1, int(request.args.get('page', 1)))
    limit = max(5, min(100, int(request.args.get('limit', 20))))
    offset = (page - 1) * limit

    if current_user['role'] == 'student':
        std = query_db("SELECT id FROM students WHERE user_id = %s", (current_user['user_id'],), one=True)
        if std:
            student_id = std['id']
        else:
            return jsonify({'records': [], 'total': 0, 'page': page, 'pages': 0})

    base_where = "WHERE 1=1"
    params = []

    if search:
        base_where += " AND (u.full_name LIKE %s OR u.username LIKE %s OR sub.subject_name LIKE %s)"
        like_str = f"%{search}%"
        params.extend([like_str, like_str, like_str])
    if start_date:
        base_where += " AND a.date >= %s"
        params.append(start_date)
    if end_date:
        base_where += " AND a.date <= %s"
        params.append(end_date)
    if subject_id:
        base_where += " AND a.subject_id = %s"
        params.append(subject_id)
    if course_id:
        base_where += " AND s.course_id = %s"
        params.append(course_id)
    if student_id:
        base_where += " AND a.student_id = %s"
        params.append(student_id)
    if status:
        base_where += " AND a.status = %s"
        params.append(status)
    if method:
        base_where += " AND a.method = %s"
        params.append(method)
    if session_id:
        base_where += " AND a.session_id = %s"
        params.append(session_id)

    # Count total records
    count_query = f"""
        SELECT COUNT(*) as total
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        JOIN users u ON s.user_id = u.id
        JOIN subjects sub ON a.subject_id = sub.id
        JOIN courses c ON s.course_id = c.id
        {base_where}
    """
    total_records = query_db(count_query, tuple(params), one=True)['total']

    # Select paginated records
    records_query = f"""
        SELECT a.id, a.session_id, a.date, a.status, a.method, a.created_at,
               s.id as student_id, u.full_name as student_name, u.username as student_username, u.email as student_email,
               sub.subject_name, c.course_name, tu.full_name as teacher_name
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        JOIN users u ON s.user_id = u.id
        JOIN subjects sub ON a.subject_id = sub.id
        JOIN courses c ON s.course_id = c.id
        LEFT JOIN users tu ON a.teacher_id = tu.id
        {base_where}
        ORDER BY a.date DESC, a.id DESC
        LIMIT %s OFFSET %s
    """
    paginated_params = params + [limit, offset]
    records = query_db(records_query, tuple(paginated_params))
    total_pages = int(np.ceil(total_records / limit)) if total_records > 0 else 1

    return jsonify({
        'records': records,
        'data': records,
        'total': total_records,
        'page': page,
        'limit': limit,
        'total_pages': total_pages,
        'pagination': {
            'page': page,
            'limit': limit,
            'total_records': total_records,
            'total_pages': total_pages
        }
    })



# ----------------- Course & Subject Endpoints -----------------

@app.route('/api/courses', methods=['GET', 'POST'])
@token_required
def manage_courses(current_user):
    if request.method == 'POST':
        if current_user['role'] != 'admin':
            return jsonify({'message': 'Unauthorized'}), 403
        data = request.json
        if not data or not data.get('course_name'):
            return jsonify({'message': 'Course name is required'}), 400
        
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO courses (course_name) VALUES (%s)", (data['course_name'],))
                res_id = cur.lastrowid
            return jsonify({'message': 'Course created successfully', 'id': res_id}), 201
        except pymysql.err.IntegrityError:
            return jsonify({'message': 'Course name already exists'}), 400
        except Exception as e:
            return jsonify({'message': str(e)}), 500
        finally:
            conn.close()

    return jsonify(query_db("SELECT * FROM courses ORDER BY id ASC"))

@app.route('/api/subjects', methods=['GET', 'POST'])
@token_required
def manage_subjects(current_user):
    if request.method == 'POST':
        if current_user['role'] != 'admin':
            return jsonify({'message': 'Unauthorized'}), 403
        data = request.json
        if not data or not data.get('subject_name') or not data.get('course_id'):
            return jsonify({'message': 'Subject name and course_id are required'}), 400

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO subjects (subject_name, course_id) VALUES (%s, %s)", (data['subject_name'], data['course_id']))
                res_id = cur.lastrowid
            return jsonify({'message': 'Subject created successfully', 'id': res_id}), 201
        except Exception as e:
            return jsonify({'message': str(e)}), 500
        finally:
            conn.close()
    
    course_id = request.args.get('course_id')
    if course_id:
        return jsonify(query_db("SELECT * FROM subjects WHERE course_id = %s ORDER BY id ASC", (course_id,)))
    return jsonify(query_db("SELECT s.*, c.course_name FROM subjects s JOIN courses c ON s.course_id = c.id ORDER BY s.id ASC"))

# ----------------- Report Endpoints -----------------

@app.route('/api/reports/attendance', methods=['GET'])
@token_required
def get_attendance_report(current_user):
    subject_id = request.args.get('subject_id')
    date = request.args.get('date')
    student_id = request.args.get('student_id')
    course_id = request.args.get('course_id')
    
    if current_user['role'] == 'student':
        std = query_db("SELECT id FROM students WHERE user_id = %s", (current_user['user_id'],), one=True)
        if std:
            student_id = std['id']
        else:
            return jsonify([])

    query = """
        SELECT a.id, a.date, a.status, a.method, a.created_at,
               s.id as student_id, u.full_name as student_name, u.email as student_email,
               sub.subject_name, c.course_name
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        JOIN users u ON s.user_id = u.id
        JOIN subjects sub ON a.subject_id = sub.id
        JOIN courses c ON s.course_id = c.id
        WHERE 1=1
    """
    params = []
    if course_id:
        query += " AND s.course_id = %s"
        params.append(course_id)
    if subject_id:
        query += " AND a.subject_id = %s"
        params.append(subject_id)
    if date:
        query += " AND a.date = %s"
        params.append(date)
    if student_id:
        query += " AND a.student_id = %s"
        params.append(student_id)
        
    query += " ORDER BY a.date DESC, a.id DESC"
    return jsonify(query_db(query, tuple(params)))

# ----------------- Dashboard Analytics Endpoints -----------------

@app.route('/api/stats/dashboard', methods=['GET'])
@token_required
def get_dashboard_stats(current_user):
    try:
        total_students = query_db("SELECT COUNT(*) as count FROM students", one=True)['count']
        total_courses = query_db("SELECT COUNT(*) as count FROM courses", one=True)['count']
        total_subjects = query_db("SELECT COUNT(*) as count FROM subjects", one=True)['count']
        total_teachers = query_db("SELECT COUNT(*) as count FROM users WHERE role = 'teacher'", one=True)['count']
        
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        today_attendance = query_db(
            "SELECT status, COUNT(*) as count FROM attendance WHERE date = %s GROUP BY status",
            (today_str,)
        )
        today_present = sum(row['count'] for row in today_attendance if row['status'] == 'Present')
        today_total = sum(row['count'] for row in today_attendance)
        
        all_att = query_db("SELECT status, COUNT(*) as count FROM attendance GROUP BY status")
        all_present = sum(row['count'] for row in all_att if row['status'] == 'Present')
        all_total = sum(row['count'] for row in all_att)
        avg_rate = round((all_present / all_total * 100), 1) if all_total > 0 else 92.5
        
        # Weekly trends (last 7 days)
        weekly_trends = query_db("""
            SELECT date, 
                   SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) as present_count,
                   COUNT(*) as total_count
            FROM attendance
            GROUP BY date
            ORDER BY date DESC
            LIMIT 7
        """)

        return jsonify({
            'total_students': total_students,
            'total_courses': total_courses,
            'total_subjects': total_subjects,
            'total_teachers': total_teachers,
            'today_present': today_present,
            'today_total': today_total,
            'avg_rate': avg_rate,
            'weekly_trends': weekly_trends[::-1]
        })
    except Exception as e:
        logger.error(f"Stats calculation error: {e}")
        return jsonify({'message': str(e)}), 500

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Starting Attendify backend on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=True)

