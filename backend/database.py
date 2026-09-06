import os
import pymysql
import pymysql.cursors
import certifi
import bcrypt
import json
import logging
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

DB_HOST = os.getenv('DB_HOST', 'gateway01.ap-southeast-1.prod.aws.tidbcloud.com')
DB_PORT = int(os.getenv('DB_PORT', 4000))
DB_USER = os.getenv('DB_USER', '2eAouK5J29qLK5G.root')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'jWBZIi1sWAV6dWpx')
DB_NAME = os.getenv('DB_NAME', 'attendance_db')
DB_SSL = os.getenv('DB_SSL', 'true').lower() in ('true', '1', 'yes')

_cached_db_conn = None

def get_db_connection(use_db=True, max_retries=3):
    """Establishes or reuses a high-performance TLS connection to TiDB Cloud."""
    global _cached_db_conn
    
    if use_db and _cached_db_conn is not None:
        try:
            _cached_db_conn.ping(reconnect=True)
            return _cached_db_conn
        except Exception:
            _cached_db_conn = None

    ssl_config = {'ca': certifi.where()} if DB_SSL else None
    
    for attempt in range(1, max_retries + 1):
        try:
            conn = pymysql.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                database=DB_NAME if use_db else None,
                ssl=ssl_config,
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
                connect_timeout=10,
                read_timeout=15,
                write_timeout=15,
                charset='utf8mb4'
            )
            if use_db:
                _cached_db_conn = conn
            return conn
        except pymysql.MySQLError as err:
            logger.warning(f"TiDB Cloud connection attempt {attempt}/{max_retries} failed: {err}")
            if attempt < max_retries:
                time.sleep(1.0)
            else:
                logger.error(f"Failed to connect to TiDB Cloud at {DB_HOST}:{DB_PORT} after {max_retries} attempts.")
                raise



def init_db():
    """Initializes the database schema and seeds initial data if missing."""
    try:
        # Step 1: Ensure database exists
        admin_conn = get_db_connection(use_db=False)
        with admin_conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        admin_conn.close()

        # Step 2: Connect to database and create tables
        conn = get_db_connection(use_db=True)
        with conn.cursor() as cursor:
            # Users table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                email VARCHAR(150) UNIQUE NOT NULL,
                role VARCHAR(50) NOT NULL,
                full_name VARCHAR(150) NOT NULL,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Courses table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS courses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                course_name VARCHAR(150) UNIQUE NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Subjects table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                id INT AUTO_INCREMENT PRIMARY KEY,
                subject_name VARCHAR(150) NOT NULL,
                course_id INT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Students table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                course_id INT NOT NULL,
                face_encoding LONGTEXT,
                is_active TINYINT(1) DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Auto-migrate is_active columns for existing tables
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN is_active TINYINT(1) DEFAULT 1;")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE students ADD COLUMN is_active TINYINT(1) DEFAULT 1;")
            except Exception:
                pass

            # Attendance Sessions table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS attendance_sessions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                subject_id INT NOT NULL,
                teacher_id INT NOT NULL,
                session_code VARCHAR(50) UNIQUE NOT NULL,
                date VARCHAR(20) NOT NULL,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP NULL,
                status VARCHAR(20) DEFAULT 'ACTIVE',
                total_marked INT DEFAULT 0,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_sess_lookup (subject_id, status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Attendance table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id INT NULL,
                student_id INT NOT NULL,
                subject_id INT NOT NULL,
                teacher_id INT NOT NULL,
                date VARCHAR(20) NOT NULL,
                status VARCHAR(20) NOT NULL,
                method VARCHAR(50) DEFAULT 'Manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
                FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_att_lookup (student_id, subject_id, date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Auto-migrate session_id in attendance
            try:
                cursor.execute("ALTER TABLE attendance ADD COLUMN session_id INT NULL;")
            except Exception:
                pass

            # Liveness Verification Audit table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS liveness_attempts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id INT NULL,
                student_id INT NULL,
                subject_id INT NOT NULL,
                liveness_status VARCHAR(30) NOT NULL,
                liveness_type VARCHAR(50) DEFAULT 'TEXTURE_AND_MOTION',
                score FLOAT DEFAULT 1.0,
                details VARCHAR(255),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_live_student (student_id, timestamp)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Suspicious Activity & Anomaly Detection table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS suspicious_activities (
                id INT AUTO_INCREMENT PRIMARY KEY,
                student_id INT NULL,
                student_name VARCHAR(150),
                activity_type VARCHAR(100) NOT NULL,
                severity VARCHAR(20) DEFAULT 'MEDIUM',
                details TEXT,
                resolved TINYINT(1) DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_susp_type (activity_type, timestamp)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # --- SEED DATA ---
            # 0. Initial Admin
            cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1;")
            if not cursor.fetchone():
                hashed = bcrypt.hashpw('admin123'.encode('utf8'), bcrypt.gensalt()).decode('utf8')
                cursor.execute(
                    "INSERT INTO users (username, password, email, role, full_name) VALUES (%s, %s, %s, %s, %s)",
                    ('admin', hashed, 'admin@attendify.com', 'admin', 'System Administrator')
                )
                logger.info("Default Admin created (admin / admin123)")


            # Initial Teacher
            cursor.execute("SELECT id FROM users WHERE role = 'teacher' LIMIT 1;")
            if not cursor.fetchone():
                hashed_t = bcrypt.hashpw('teacher123'.encode('utf8'), bcrypt.gensalt()).decode('utf8')
                cursor.execute(
                    "INSERT INTO users (username, password, email, role, full_name) VALUES (%s, %s, %s, %s, %s)",
                    ('prof_smith', hashed_t, 'smith@attendify.com', 'teacher', 'Prof. Robert Smith')
                )
                logger.info("Default Teacher created (prof_smith / teacher123)")

            # 1. Create Courses
            cursor.execute("SELECT COUNT(*) AS cnt FROM courses;")
            if cursor.fetchone()['cnt'] == 0:
                courses = [('Computer Science 101',), ('Business Administration',), ('Artificial Intelligence',)]
                cursor.executemany("INSERT INTO courses (course_name) VALUES (%s)", courses)
                logger.info("Initial Courses seeded.")

            # 2. Create Subjects
            cursor.execute("SELECT COUNT(*) AS cnt FROM subjects;")
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute("SELECT id, course_name FROM courses;")
                course_map = {row['course_name']: row['id'] for row in cursor.fetchall()}
                
                cs_id = course_map.get('Computer Science 101', 1)
                ba_id = course_map.get('Business Administration', 2)
                ai_id = course_map.get('Artificial Intelligence', cs_id)

                subjects = [
                    ('Data Structures & Algorithms', cs_id),
                    ('Web Engineering & APIs', cs_id),
                    ('Database Management Systems', cs_id),
                    ('Financial Accounting', ba_id),
                    ('Strategic Marketing', ba_id),
                    ('Neural Networks & Deep Learning', ai_id)
                ]
                cursor.executemany("INSERT INTO subjects (subject_name, course_id) VALUES (%s, %s)", subjects)
                logger.info("Initial Subjects seeded.")

            # 3. Create Students
            cursor.execute("SELECT COUNT(*) AS cnt FROM students;")
            if cursor.fetchone()['cnt'] == 0:
                hashed_pw = bcrypt.hashpw('student123'.encode('utf8'), bcrypt.gensalt()).decode('utf8')
                mock_encoding = json.dumps([0.05 * (i % 5) for i in range(128)])

                cursor.execute("SELECT id, course_name FROM courses;")
                course_map = {row['course_name']: row['id'] for row in cursor.fetchall()}
                cs_id = course_map.get('Computer Science 101', 1)
                ba_id = course_map.get('Business Administration', 2)

                student_data = [
                    ('alex_j', 'Alex Johnson', 'alex@student.edu', cs_id),
                    ('maria_g', 'Maria Garcia', 'maria@student.edu', cs_id),
                    ('liam_s', 'Liam Smith', 'liam@student.edu', cs_id),
                    ('sophia_b', 'Sophia Brown', 'sophia@student.edu', cs_id),
                    ('ethan_d', 'Ethan Davis', 'ethan@student.edu', cs_id),
                    ('olivia_w', 'Olivia Wilson', 'olivia@student.edu', ba_id),
                    ('noah_m', 'Noah Martinez', 'noah@student.edu', ba_id),
                    ('emma_a', 'Emma Anderson', 'emma@student.edu', ba_id),
                    ('james_t', 'James Taylor', 'james@student.edu', ba_id),
                    ('isabella_t', 'Isabella Thomas', 'isabella@student.edu', ba_id),
                ]

                for username, name, email, cid in student_data:
                    cursor.execute(
                        "INSERT INTO users (username, password, email, role, full_name) VALUES (%s, %s, %s, %s, %s)",
                        (username, hashed_pw, email, 'student', name)
                    )
                    uid = cursor.lastrowid
                    cursor.execute(
                        "INSERT INTO students (user_id, course_id, face_encoding) VALUES (%s, %s, %s)",
                        (uid, cid, mock_encoding)
                    )
                logger.info("10 Initial Demo Students seeded.")

            # 4. Seed Multi-Session Attendance History for AI Analytics (if attendance < 15 records)
            cursor.execute("SELECT COUNT(*) AS cnt FROM attendance;")
            if cursor.fetchone()['cnt'] < 15:
                cursor.execute("SELECT s.id as student_id, s.course_id, u.full_name FROM students s JOIN users u ON s.user_id = u.id LIMIT 10;")
                all_stds = cursor.fetchall()
                cursor.execute("SELECT id, course_id FROM subjects;")
                all_subs = cursor.fetchall()
                cursor.execute("SELECT id FROM users WHERE role = 'teacher' LIMIT 1;")
                t_row = cursor.fetchone()
                teacher_id = t_row['id'] if t_row else 1

                import datetime
                base_date = datetime.date.today() - datetime.timedelta(days=12)
                
                # Create sample sessions
                for day_offset in range(10):
                    sess_date = (base_date + datetime.timedelta(days=day_offset)).strftime('%Y-%m-%d')
                    for sub in all_subs[:2]:
                        cursor.execute(
                            "INSERT INTO attendance_sessions (subject_id, teacher_id, session_code, date, status, total_marked) VALUES (%s, %s, %s, %s, 'ENDED', 5)",
                            (sub['id'], teacher_id, f"SESS-{sess_date}-{sub['id']}", sess_date)
                        )
                        sess_id = cursor.lastrowid

                        # Mark student attendance with varied patterns (high, medium, low risk)
                        for idx, std in enumerate(all_stds):
                            if std['course_id'] == sub['course_id']:
                                # Create variance for AI trend detection:
                                # Student 0,1: High attendance (90%+) -> LOW risk
                                # Student 2: Declining attendance -> MEDIUM/HIGH risk
                                # Student 3: Consistently low (50%) -> HIGH risk
                                if idx in (0, 1):
                                    status = 'Present' if day_offset != 3 else 'Absent'
                                elif idx == 2:
                                    status = 'Present' if day_offset < 4 else 'Absent'
                                elif idx == 3:
                                    status = 'Absent' if day_offset % 2 == 0 else 'Present'
                                else:
                                    status = 'Present' if day_offset % 3 != 0 else 'Absent'

                                cursor.execute(
                                    "INSERT INTO attendance (session_id, student_id, subject_id, teacher_id, date, status, method) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                    (sess_id, std['student_id'], sub['id'], teacher_id, sess_date, status, 'FaceRecognition' if status == 'Present' else 'Manual')
                                )
                logger.info("Multi-Session AI Attendance History seeded successfully.")

            # 5. Seed Initial Suspicious Activities
            cursor.execute("SELECT COUNT(*) AS cnt FROM suspicious_activities;")
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute("""
                INSERT INTO suspicious_activities (student_name, activity_type, severity, details) VALUES
                ('Alex Johnson', 'REPEATED_LIVENESS_FAILURE', 'MEDIUM', 'Multiple failed liveness checks (static photo texture detected) during Web Engineering session'),
                ('Ethan Davis', 'DUPLICATE_SCAN_ATTEMPT', 'LOW', 'Duplicate biometric recognition attempt recorded 12s after initial verification'),
                ('Unknown Face', 'UNRECOGNIZED_FACE_FLOOD', 'HIGH', '5 consecutive unmapped biometric captures detected at terminal without match')
                """)
                logger.info("Initial Suspicious Activities seeded.")

        conn.close()
        logger.info("TiDB Cloud database initialized successfully!")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

if __name__ == '__main__':
    init_db()


