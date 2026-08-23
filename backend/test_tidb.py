import os, pymysql, certifi
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

db_host = os.getenv('DB_HOST')
db_port = int(os.getenv('DB_PORT', 4000))
db_user = os.getenv('DB_USER')
db_password = os.getenv('DB_PASSWORD')
db_name = os.getenv('DB_NAME', 'attendance_db')

print(f"Connecting to {db_host}:{db_port} as {db_user}...")

conn = pymysql.connect(
    host=db_host,
    port=db_port,
    user=db_user,
    password=db_password,
    ssl={'ca': certifi.where()},
    autocommit=True
)

cursor = conn.cursor()
cursor.execute('SELECT VERSION();')
version = cursor.fetchone()
print('Connected successfully! TiDB Server Version:', version)

cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name};")
print(f"Database '{db_name}' ensured.")
cursor.close()
conn.close()
