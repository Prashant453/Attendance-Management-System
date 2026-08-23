import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from database import init_db, get_db_connection

if __name__ == '__main__':
    print('Testing TiDB Cloud Connection & Initializing...')
    init_db()
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('SHOW TABLES;')
        tables = cur.fetchall()
        print('Tables in TiDB Cloud:')
        for t in tables:
            print(' -', list(t.values())[0])
    conn.close()
    print('TiDB Cloud verification complete!')
