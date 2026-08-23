import os
import pymysql
import pymysql.cursors
import certifi
import bcrypt
import json
import logging
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'backend', '.env'))
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_HOST = os.getenv('DB_HOST', 'gateway01.ap-southeast-1.prod.aws.tidbcloud.com')
DB_PORT = int(os.getenv('DB_PORT', 4000))
DB_USER = os.getenv('DB_USER', '2eAouK5J29qLK5G.root')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_NAME = os.getenv('DB_NAME', 'attendance_db')
DB_SSL = os.getenv('DB_SSL', 'true').lower() in ('true', '1', 'yes')

def get_db_connection(use_db=True):
    """Establishes a secure connection to TiDB Cloud."""
    try:
        ssl_config = {'ca': certifi.where()} if DB_SSL else None
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
            charset='utf8mb4'
        )
        return conn
    except pymysql.MySQLError as err:
        logger.error(f"TiDB Cloud Connection Error: {err}")
        raise

def init_db():
    """Initializes the database schema and seeds initial data if missing."""
    try:
        admin_conn = get_db_connection(use_db=False)
        with admin_conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        admin_conn.close()

        conn = get_db_connection(use_db=True)
        with conn.cursor() as cursor:
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

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS courses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                course_name VARCHAR(150) UNIQUE NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                id INT AUTO_INCREMENT PRIMARY KEY,
                subject_name VARCHAR(150) NOT NULL,
                course_id INT NOT NULL,
                FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

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

            # Seed Admin if not exists
            cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1;")
            if not cursor.fetchone():
                hashed = bcrypt.hashpw('admin123'.encode('utf8'), bcrypt.gensalt()).decode('utf8')
                cursor.execute(
                    "INSERT INTO users (username, password, email, role, full_name) VALUES (%s, %s, %s, %s, %s)",
                    ('admin', hashed, 'admin@attendify.com', 'admin', 'System Administrator')
                )

        conn.close()
    except Exception as e:
        logger.error(f"Database init failed: {e}")

if __name__ == "__main__":
    init_db()

