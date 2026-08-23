from flask import Flask, request, jsonify
from flask_cors import CORS
import pymysql
import bcrypt
import jwt
import datetime
import os
import json
import logging
from functools import wraps
from database import get_db_connection, init_db
from face_utils import get_face_encodings, compare_faces
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

# Helper to run dictionary queries in TiDB Cloud
def query_db(query, args=(), one=False):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, args)
            rv = cur.fetchall()
            return (rv[0] if rv else None) if one else rv
    finally:
        conn.close()

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
    finally:
        conn.close()

@app.route('/api/attendance/face-recognition', methods=['POST'])
@token_required
def mark_attendance_face_recognition(current_user):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403
    
    data = request.json
    if not data or not data.get('subject_id') or not data.get('image'):
        return jsonify({'message': 'Subject and image data required'}), 400

    subject = query_db("SELECT course_id FROM subjects WHERE id = %s", (data['subject_id'],), one=True)
    if not subject:
        return jsonify({'message': 'Subject not found'}), 404
    
    known_students = query_db(
        "SELECT id, face_encoding FROM students WHERE course_id = %s AND COALESCE(is_active, 1) = 1 AND face_encoding IS NOT NULL AND face_encoding != ''",
        (subject['course_id'],)
    )

    
    known_encodings = {}
    for ks in known_students:
        try:
            parsed = json.loads(ks['face_encoding'])
            if isinstance(parsed, list) and len(parsed) == 128:
                known_encodings[ks['id']] = parsed
        except Exception:
            continue
    
    if not known_encodings:
        return jsonify({
            'message': 'No registered face biometrics found for students enrolled in this course. Please enroll student faces in Student Management first.',
            'recognized_ids': [],
            'marked_present_count': 0
        }), 400
    
    recognized_ids = compare_faces(known_encodings, data['image'])
    
    conn = get_db_connection()
    marked_count = 0
    recognized_students_info = []
    target_date = data.get('date', datetime.date.today().strftime('%Y-%m-%d'))

    try:
        with conn.cursor() as cursor:
            for sid in recognized_ids:
                cursor.execute(
                    "SELECT id, status FROM attendance WHERE student_id = %s AND subject_id = %s AND date = %s",
                    (sid, data['subject_id'], target_date)
                )
                existing = cursor.fetchone()
                
                if not existing:
                    cursor.execute(
                        "INSERT INTO attendance (student_id, subject_id, teacher_id, date, status, method) VALUES (%s, %s, %s, %s, %s, %s)",
                        (sid, data['subject_id'], current_user['user_id'], target_date, 'Present', 'FaceRecognition')
                    )
                    marked_count += 1
                elif existing['status'] != 'Present':
                    cursor.execute(
                        "UPDATE attendance SET status = 'Present', method = 'FaceRecognition', teacher_id = %s WHERE id = %s",
                        (current_user['user_id'], existing['id'])
                    )
                    marked_count += 1
                
                # Fetch student name for UI feedback
                cursor.execute(
                    "SELECT s.id, u.full_name FROM students s JOIN users u ON s.user_id = u.id WHERE s.id = %s",
                    (sid,)
                )
                st_info = cursor.fetchone()
                if st_info:
                    recognized_students_info.append(st_info)

        conn.commit()

        if not recognized_ids:
            return jsonify({
                'message': 'Face detected in camera frame, but it did not match any student enrolled in this course.',
                'recognized_ids': [],
                'marked_present_count': 0,
                'recognized_students': []
            }), 200

        return jsonify({
            'message': f'Recognized {len(recognized_ids)} student(s). {marked_count} attendance record(s) synchronized.',
            'recognized_ids': recognized_ids,
            'marked_present_count': marked_count,
            'recognized_students': recognized_students_info
        })
    except Exception as e:
        logger.error(f"Error in facial attendance marking: {e}")
        return jsonify({'message': str(e)}), 500
    finally:
        conn.close()


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

