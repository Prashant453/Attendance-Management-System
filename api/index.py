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

try:
    from .database import get_db_connection, init_db
    from .face_utils import get_face_encodings, compare_faces
except ImportError:
    from database import get_db_connection, init_db
    from face_utils import get_face_encodings, compare_faces

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'attendify_super_secure_jwt_secret_key_2026')

def query_db(query, args=(), one=False):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, args)
            rv = cur.fetchall()
            return (rv[0] if rv else None) if one else rv
    finally:
        conn.close()

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'x-access-token' in request.headers:
            token = request.headers['x-access-token']
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = data
        except Exception:
            return jsonify({'message': 'Token is invalid or expired!'}), 401
        return f(current_user, *args, **kwargs)
    return decorated

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'database': 'TiDB Cloud'}), 200

@app.route('/api/login', methods=['POST'])
def login():
    auth = request.json
    if not auth or not auth.get('username') or not auth.get('password'):
        return jsonify({'message': 'Missing credentials'}), 400
    user = query_db("SELECT * FROM users WHERE username = %s", (auth['username'],), one=True)
    if user and bcrypt.checkpw(auth['password'].encode('utf8'), user['password'].encode('utf8')):
        token = jwt.encode({
            'user_id': user['id'], 'username': user['username'], 'role': user['role'],
            'full_name': user['full_name'], 'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        return jsonify({'token': token, 'role': user['role'], 'full_name': user['full_name'], 'user_id': user['id']})
    return jsonify({'message': 'Invalid credentials'}), 401

@app.route('/api/students', methods=['GET', 'POST'])
@token_required
def manage_students(current_user):
    if request.method == 'POST':
        if current_user['role'] != 'admin':
            return jsonify({'message': 'Unauthorized'}), 403
        data = request.json
        if not data or not data.get('username') or not data.get('password') or not data.get('course_id'):
            return jsonify({'message': 'Missing required student fields'}), 400
        hashed_pw = bcrypt.hashpw(data['password'].encode('utf8'), bcrypt.gensalt()).decode('utf8')
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO users (username, password, email, role, full_name, is_active) VALUES (%s, %s, %s, %s, %s, 1)",
                            (data['username'], hashed_pw, data.get('email', f"{data['username']}@student.edu"), 'student', data.get('full_name', data['username'])))
                user_id = cur.lastrowid
                cur.execute("INSERT INTO students (user_id, course_id, is_active) VALUES (%s, %s, 1)", (user_id, data['course_id']))
                student_id = cur.lastrowid
            conn.commit()
            return jsonify({'message': 'Student created successfully', 'id': student_id}), 201
        except Exception as e:
            return jsonify({'message': str(e)}), 400
        finally:
            conn.close()
    
    course_id = request.args.get('course_id')
    status = request.args.get('status')
    
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

@app.route('/api/students/<int:student_id>', methods=['GET', 'PUT'])
@token_required
def single_student(current_user, student_id):
    if current_user['role'] not in ['admin', 'teacher']:
        return jsonify({'message': 'Unauthorized'}), 403

    if request.method == 'PUT':
        if current_user['role'] != 'admin':
            return jsonify({'message': 'Unauthorized'}), 403
        data = request.json
        student = query_db("SELECT id, user_id FROM students WHERE id = %s", (student_id,), one=True)
        if not student:
            return jsonify({'message': 'Student not found'}), 404
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                user_updates, user_params = [], []
                if 'full_name' in data: user_updates.append("full_name = %s"); user_params.append(data['full_name'])
                if 'email' in data: user_updates.append("email = %s"); user_params.append(data['email'])
                if 'username' in data: user_updates.append("username = %s"); user_params.append(data['username'])
                if 'password' in data and data['password']:
                    user_updates.append("password = %s")
                    user_params.append(bcrypt.hashpw(data['password'].encode('utf8'), bcrypt.gensalt()).decode('utf8'))
                if user_updates:
                    user_params.append(student['user_id'])
                    cur.execute(f"UPDATE users SET {', '.join(user_updates)} WHERE id = %s", tuple(user_params))
                if 'course_id' in data:
                    cur.execute("UPDATE students SET course_id = %s WHERE id = %s", (data['course_id'], student_id))
            conn.commit()
            return jsonify({'message': 'Updated successfully', 'success': True})
        except Exception as e:
            return jsonify({'message': str(e)}), 400
        finally:
            conn.close()

    query = """
        SELECT s.id, s.user_id, u.username, u.email, u.full_name, c.course_name, s.course_id, 
               COALESCE(s.is_active, 1) as is_active, u.created_at,
               (s.face_encoding IS NOT NULL AND s.face_encoding != '') as face_registered
        FROM students s JOIN users u ON s.user_id = u.id JOIN courses c ON s.course_id = c.id WHERE s.id = %s
    """
    student = query_db(query, (student_id,), one=True)
    if not student:
        return jsonify({'message': 'Student not found'}), 404
    student['face_registered'] = bool(student['face_registered'])
    student['is_active'] = bool(student['is_active'])

    att_stats = query_db("SELECT status, COUNT(*) as count FROM attendance WHERE student_id = %s GROUP BY status", (student_id,))
    present_count = sum(r['count'] for r in att_stats if r['status'] == 'Present')
    total_classes = sum(r['count'] for r in att_stats)
    rate = round((present_count / total_classes * 100), 1) if total_classes > 0 else 0.0

    student['stats'] = {
        'total_classes': total_classes,
        'present_count': present_count,
        'absent_count': total_classes - present_count,
        'attendance_percentage': rate
    }
    return jsonify(student)

@app.route('/api/students/<int:student_id>/status', methods=['PATCH'])
@token_required
def toggle_status(current_user, student_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized'}), 403
    data = request.json
    new_status = 1 if data.get('is_active') in (1, True, '1', 'true') else 0
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE students SET is_active = %s WHERE id = %s", (new_status, student_id))
            cur.execute("UPDATE users SET is_active = %s WHERE id = %s", (new_status, student_id))
        conn.commit()
        return jsonify({'message': 'Status updated', 'is_active': bool(new_status), 'success': True})
    finally:
        conn.close()

@app.route('/api/students/<int:student_id>/face', methods=['DELETE'])
@token_required
def clear_face(current_user, student_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized'}), 403
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE students SET face_encoding = NULL WHERE id = %s", (student_id,))
        conn.commit()
        return jsonify({'message': 'Face data removed', 'success': True})
    finally:
        conn.close()

@app.route('/api/students/<int:student_id>/attendance', methods=['GET'])
@token_required
def student_history(current_user, student_id):
    query = """
        SELECT a.id, a.date, a.status, a.method, a.created_at, sub.subject_name, c.course_name, u.full_name as teacher_name
        FROM attendance a
        JOIN subjects sub ON a.subject_id = sub.id
        JOIN courses c ON sub.course_id = c.id
        LEFT JOIN users u ON a.teacher_id = u.id
        WHERE a.student_id = %s ORDER BY a.date DESC, a.id DESC
    """
    return jsonify(query_db(query, (student_id,)))


@app.route('/api/students/<int:student_id>/register-face', methods=['POST'])
@token_required
def register_face(current_user, student_id):
    if current_user['role'] != 'admin':
        return jsonify({'message': 'Unauthorized'}), 403
    
    data = request.json
    encoding = get_face_encodings(data['image'])
    if not encoding:
        return jsonify({'message': 'No face detected'}), 400
    
    encoding_json = json.dumps(encoding)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE students SET face_encoding = %s WHERE id = %s", (encoding_json, student_id))
        return jsonify({'message': 'Face data registered successfully', 'success': True})
    finally:
        conn.close()

@app.route('/api/attendance/manual', methods=['POST'])
@token_required
def mark_attendance_manual(current_user):
    data = request.json
    if not data or not data.get('student_id') or not data.get('subject_id') or not data.get('date'):
        return jsonify({'message': 'Missing required fields: student_id, subject_id, date'}), 400

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
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM attendance WHERE student_id = %s AND subject_id = %s AND date = %s", 
                        (data['student_id'], data['subject_id'], data['date']))
            existing = cur.fetchone()
            if existing: 
                cur.execute("UPDATE attendance SET status = %s, teacher_id = %s, method = 'Manual' WHERE id = %s", 
                            (data['status'], current_user['user_id'], existing['id']))
            else: 
                cur.execute("INSERT INTO attendance (student_id, subject_id, teacher_id, date, status, method) VALUES (%s, %s, %s, %s, %s, %s)", 
                            (data['student_id'], data['subject_id'], current_user['user_id'], data['date'], data['status'], 'Manual'))
        conn.commit()
        return jsonify({'message': 'Attendance marked successfully', 'status': data['status']})
    finally:
        conn.close()


@app.route('/api/attendance/face-recognition', methods=['POST'])
@token_required
def mark_attendance_face_recognition(current_user):
    data = request.json
    subject = query_db("SELECT course_id FROM subjects WHERE id = %s", (data['subject_id'],), one=True)
    if not subject:
        return jsonify({'message': 'Subject not found'}), 404
        
    known_students = query_db("SELECT id, face_encoding FROM students WHERE course_id = %s AND face_encoding IS NOT NULL AND face_encoding != ''", (subject['course_id'],))
    known_encodings = {}
    for ks in known_students:
        try:
            known_encodings[ks['id']] = json.loads(ks['face_encoding'])
        except Exception:
            continue
    
    if not known_encodings:
        return jsonify({'message': 'No registered face data'}), 400
        
    recognized_ids = compare_faces(known_encodings, data['image'])
    
    conn = get_db_connection()
    count = 0
    try:
        with conn.cursor() as cur:
            for sid in recognized_ids:
                cur.execute("SELECT id FROM attendance WHERE student_id = %s AND subject_id = %s AND date = %s", (sid, data['subject_id'], data.get('date', datetime.date.today().strftime('%Y-%m-%d'))))
                if not cur.fetchone():
                    cur.execute("INSERT INTO attendance (student_id, subject_id, teacher_id, date, status, method) VALUES (%s, %s, %s, %s, %s, %s)", 
                                (sid, data['subject_id'], current_user['user_id'], data.get('date', datetime.date.today().strftime('%Y-%m-%d')), 'Present', 'FaceRecognition'))
                    count += 1
        return jsonify({'marked_present_count': count, 'recognized_ids': recognized_ids})
    finally:
        conn.close()

@app.route('/api/courses', methods=['GET', 'POST'])
@token_required
def manage_courses(current_user):
    if request.method == 'POST':
        if current_user['role'] != 'admin': return jsonify({'message': 'Unauthorized'}), 403
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO courses (course_name) VALUES (%s)", (request.json['course_name'],))
                res_id = cur.lastrowid
            return jsonify({'message': 'Course created', 'id': res_id}), 201
        finally:
            conn.close()
    return jsonify(query_db("SELECT * FROM courses ORDER BY id ASC"))

@app.route('/api/subjects', methods=['GET', 'POST'])
@token_required
def manage_subjects(current_user):
    if request.method == 'POST':
        if current_user['role'] != 'admin': return jsonify({'message': 'Unauthorized'}), 403
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO subjects (subject_name, course_id) VALUES (%s, %s)", (request.json['subject_name'], request.json['course_id']))
                res_id = cur.lastrowid
            return jsonify({'message': 'Subject created', 'id': res_id}), 201
        finally:
            conn.close()
    course_id = request.args.get('course_id')
    if course_id: return jsonify(query_db("SELECT * FROM subjects WHERE course_id = %s ORDER BY id ASC", (course_id,)))
    return jsonify(query_db("SELECT s.*, c.course_name FROM subjects s JOIN courses c ON s.course_id = c.id ORDER BY s.id ASC"))

@app.route('/api/reports/attendance', methods=['GET'])
@token_required
def get_attendance_report(current_user):
    sub_id = request.args.get('subject_id')
    date = request.args.get('date')
    std_id = request.args.get('student_id')
    if current_user['role'] == 'student':
        res = query_db("SELECT id FROM students WHERE user_id = %s", (current_user['user_id'],), one=True)
        std_id = res['id'] if res else None
        
    query = """
        SELECT a.id, a.date, a.status, a.method, u.full_name as student_name, sub.subject_name 
        FROM attendance a 
        JOIN students s ON a.student_id = s.id 
        JOIN users u ON s.user_id = u.id 
        JOIN subjects sub ON a.subject_id = sub.id 
        WHERE 1=1
    """
    params = []
    if sub_id: query += " AND a.subject_id = %s"; params.append(sub_id)
    if date: query += " AND a.date = %s"; params.append(date)
    if std_id: query += " AND a.student_id = %s"; params.append(std_id)
    query += " ORDER BY a.date DESC, a.id DESC"
    return jsonify(query_db(query, tuple(params)))

@app.route('/api/stats/dashboard', methods=['GET'])
@token_required
def get_dashboard_stats(current_user):
    total_students = query_db("SELECT COUNT(*) as count FROM students", one=True)['count']
    total_courses = query_db("SELECT COUNT(*) as count FROM courses", one=True)['count']
    all_att = query_db("SELECT status, COUNT(*) as count FROM attendance GROUP BY status")
    all_present = sum(row['count'] for row in all_att if row['status'] == 'Present')
    all_total = sum(row['count'] for row in all_att)
    avg_rate = round((all_present / all_total * 100), 1) if all_total > 0 else 94.0
    return jsonify({
        'total_students': total_students,
        'total_courses': total_courses,
        'avg_rate': avg_rate
    })

init_db()

def handler(request):
    return app(request)

if __name__ == '__main__':
    app.run(debug=True)
