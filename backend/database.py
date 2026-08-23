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

def get_db_connection(use_db=True, max_retries=3):
    """Establishes a secure TLS/SSL connection to TiDB Cloud with retry resilience."""
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
                connect_timeout=15,
                read_timeout=15,
                write_timeout=15,
                charset='utf8mb4'
            )
            return conn
        except pymysql.MySQLError as err:
            logger.warning(f"TiDB Cloud connection attempt {attempt}/{max_retries} failed: {err}")
            if attempt < max_retries:
                time.sleep(1.5)
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
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Attendance table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INT AUTO_INCREMENT PRIMARY KEY,
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

        conn.close()
        logger.info("TiDB Cloud database initialized successfully!")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise

if __name__ == '__main__':
    init_db()

