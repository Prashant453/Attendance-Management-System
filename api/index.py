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
        hashed_pw = bcrypt.hashpw(data['password'].encode('utf8'), bcrypt.gensalt()).decode('utf8')
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO users (username, password, email, role, full_name) VALUES (%s, %s, %s, %s, %s)",
                            (data['username'], hashed_pw, data['email'], 'student', data['full_name']))
                user_id = cur.lastrowid
                cur.execute("INSERT INTO students (user_id, course_id) VALUES (%s, %s)", (user_id, data['course_id']))
                student_id = cur.lastrowid
            return jsonify({'message': 'Student registered', 'id': student_id}), 201
        except Exception as e:
            return jsonify({'message': str(e)}), 400
        finally:
            conn.close()
    
    query = """
        SELECT s.id, s.user_id, u.username, u.email, u.full_name, c.course_name, s.course_id, 
               (s.face_encoding IS NOT NULL AND s.face_encoding != '') as face_registered 
        FROM students s 
        JOIN users u ON s.user_id = u.id 
        JOIN courses c ON s.course_id = c.id
        ORDER BY s.id ASC
    """
    students = query_db(query)
    for s in students:
        s['face_registered'] = bool(s['face_registered'])
    return jsonify(students)

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
        return jsonify({'message': 'Attendance marked'})
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
