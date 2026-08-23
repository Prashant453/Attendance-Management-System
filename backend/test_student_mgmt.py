import os
import sys
import json
import base64
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))
from app import app
from database import get_db_connection

def run_tests():
    client = app.test_client()
    print("\n===========================================================")
    print("STARTING ADMIN STUDENT MANAGEMENT INTEGRATION TEST SUITE")
    print("===========================================================")

    # 1. Admin Login
    login_res = client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
    assert login_res.status_code == 200, f"Admin login failed: {login_res.json}"
    token = login_res.json['token']
    headers = {'x-access-token': token}
    print("1. [PASS] Admin Authenticated & JWT Generated")

    # 2. Get Courses
    courses_res = client.get('/api/courses', headers=headers)
    assert courses_res.status_code == 200 and len(courses_res.json) > 0
    course_id = courses_res.json[0]['id']
    print(f"2. [PASS] Loaded Courses: {courses_res.json[0]['course_name']} (ID {course_id})")

    # 3. Create New Student
    test_username = f"std_mgmt_test_{os.getpid()}"
    new_student = {
        'username': test_username,
        'password': 'TestPassword123!',
        'email': f"{test_username}@test.edu",
        'full_name': 'Test Student Mgmt',
        'course_id': course_id
    }
    create_res = client.post('/api/students', json=new_student, headers=headers)
    assert create_res.status_code == 201, f"Student registration failed: {create_res.json}"
    student_id = create_res.json['id']
    print(f"3. [PASS] Created Student #{student_id} ({test_username})")

    # 4. Edit Student Details
    update_data = {
        'full_name': 'Test Student Mgmt Updated',
        'email': f"updated_{test_username}@test.edu",
        'username': f"upd_{test_username}",
        'course_id': course_id
    }
    put_res = client.put(f'/api/students/{student_id}', json=update_data, headers=headers)
    assert put_res.status_code == 200 and put_res.json.get('success'), f"Update failed: {put_res.json}"
    print(f"4. [PASS] Updated Student details: Name -> {update_data['full_name']}")

    # 5. Enroll Face Biometrics
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8) + 180
    import cv2
    _, buffer = cv2.imencode('.jpg', dummy_img)
    base64_img = 'data:image/jpeg;base64,' + base64.b64encode(buffer).decode('utf8')
    
    face_res = client.post(f'/api/students/{student_id}/register-face', json={'image': base64_img}, headers=headers)
    assert face_res.status_code == 200, f"Face enrollment failed: {face_res.json}"
    print("5. [PASS] Enrolled 128-D Facial Biometric Vector in TiDB Cloud")

    # 6. Mark Attendance & Verify History
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM subjects WHERE course_id = %s LIMIT 1", (course_id,))
        sub = cur.fetchone()
    conn.close()
    
    if sub:
        att_res = client.post('/api/attendance/manual', json={
            'student_id': student_id,
            'subject_id': sub['id'],
            'date': '2026-08-23',
            'status': 'Present'
        }, headers=headers)
        assert att_res.status_code == 200

        # Query Individual Attendance History
        hist_res = client.get(f'/api/students/{student_id}/attendance', headers=headers)
        assert hist_res.status_code == 200 and len(hist_res.json) > 0
        print(f"6. [PASS] Verified Attendance History: {len(hist_res.json)} records found")

    # 7. Query Comprehensive Profile & Analytics
    prof_res = client.get(f'/api/students/{student_id}', headers=headers)
    assert prof_res.status_code == 200
    p = prof_res.json
    assert p['face_registered'] is True
    assert p['stats']['total_classes'] >= 1
    assert p['stats']['attendance_percentage'] == 100.0
    print(f"7. [PASS] Profile Analytics: {p['stats']['total_classes']} Classes, {p['stats']['attendance_percentage']}% Rate")

    # 8. Soft Deactivate Student
    deact_res = client.patch(f'/api/students/{student_id}/status', json={'is_active': 0}, headers=headers)
    assert deact_res.status_code == 200 and deact_res.json.get('is_active') is False
    print(f"8. [PASS] Soft Deactivated Student #{student_id}")

    # 9. Verify Deactivated Student Login Rejection
    std_login = client.post('/api/login', json={'username': f"upd_{test_username}", 'password': 'TestPassword123!'})
    assert std_login.status_code == 403, f"Deactivated student should be rejected, got {std_login.status_code}"
    print("9. [PASS] Verified Login Guard: Deactivated Student Login Blocked (403 Forbidden)")

    # 10. Delete Face Biometric Data
    del_face_res = client.delete(f'/api/students/{student_id}/face', headers=headers)
    assert del_face_res.status_code == 200
    prof_after_del = client.get(f'/api/students/{student_id}', headers=headers).json
    assert prof_after_del['face_registered'] is False
    print("10. [PASS] Removed Facial Biometric Data (face_registered = False)")

    # 11. Reactivate Student
    react_res = client.patch(f'/api/students/{student_id}/status', json={'is_active': 1}, headers=headers)
    assert react_res.status_code == 200 and react_res.json.get('is_active') is True
    print(f"11. [PASS] Reactivated Student #{student_id}")

    # 12. Verify Reactivated Student Login Success
    std_login_ok = client.post('/api/login', json={'username': f"upd_{test_username}", 'password': 'TestPassword123!'})
    assert std_login_ok.status_code == 200 and 'token' in std_login_ok.json
    print("12. [PASS] Verified Login: Reactivated Student Login Successful (200 OK)")

    # Clean up test user
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users WHERE username = %s", (f"upd_{test_username}",))
    conn.close()

    print("===========================================================")
    print("ALL 12 ADMIN STUDENT MANAGEMENT INTEGRATION TESTS PASSED 100%!")
    print("===========================================================\n")

if __name__ == '__main__':
    run_tests()
