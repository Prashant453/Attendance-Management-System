import os
import sys
import json
import base64
import numpy as np
import cv2

# Set stdout encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ensure backend imports
sys.path.insert(0, os.path.dirname(__file__))
from app import app
from database import get_db_connection

def create_synthetic_face_image():
    """Generates a synthetic realistic test image with variance."""
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    for i in range(300):
        for j in range(300):
            img[i, j] = [(i * 2) % 255, (j * 3) % 255, ((i + j) * 4) % 255]
    cv2.circle(img, (150, 150), 60, (200, 180, 160), -1)
    cv2.circle(img, (130, 130), 8, (50, 50, 50), -1)
    cv2.circle(170, (170, 130), 8, (50, 50, 50), -1) if hasattr(cv2, 'circle') else None
    _, buffer = cv2.imencode('.jpg', img)
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode('utf-8')

def run_tests():
    client = app.test_client()
    print("==================================================")
    print("   ATTENTIFY AI ATTENDANCE UPGRADE TEST SUITE     ")
    print("==================================================")

    # 1. Login as Admin
    login_res = client.post('/api/login', json={
        'username': 'admin',
        'password': 'admin123'
    })
    assert login_res.status_code == 200, f"Admin login failed: {login_res.data}"
    admin_token = login_res.get_json()['token']
    auth_headers = {'Authorization': f'Bearer {admin_token}', 'x-access-token': admin_token}
    print("[PASS] 1. Admin Authentication")

    # 2. Test Session Lifecycle (Start -> Pause -> Resume -> End)
    sub_res = client.get('/api/subjects', headers=auth_headers)
    subjects = sub_res.get_json()
    assert len(subjects) > 0, "No subjects found"
    test_subject_id = subjects[0]['id']

    start_res = client.post('/api/sessions/start', json={
        'subject_id': test_subject_id
    }, headers=auth_headers)
    assert start_res.status_code in [200, 201], f"Session start failed: {start_res.data}"
    session_data = start_res.get_json()
    session_id = session_data['session']['id']
    print(f"[PASS] 2.1 Session Started (Session ID: {session_id}, Subject: {test_subject_id})")

    active_res = client.get('/api/sessions/active', headers=auth_headers)
    assert active_res.status_code == 200
    active_data = active_res.get_json()
    assert active_data.get('active_session') is not None
    assert active_data['active_session']['id'] == session_id
    print(f"[PASS] 2.2 Active Session Query Verified (Status: {active_data['active_session']['status']})")

    # Ensure session is active
    initial_status = active_data['active_session']['status']
    if initial_status == 'PAUSED':
        client.post(f'/api/sessions/{session_id}/pause', headers=auth_headers)

    # Pause session
    pause_res = client.post(f'/api/sessions/{session_id}/pause', headers=auth_headers)
    assert pause_res.status_code == 200
    assert pause_res.get_json()['status'] == 'PAUSED'
    print("[PASS] 2.3 Session Paused")

    # Resume session
    resume_res = client.post(f'/api/sessions/{session_id}/pause', headers=auth_headers)
    assert resume_res.status_code == 200
    assert resume_res.get_json()['status'] == 'ACTIVE'
    print("[PASS] 2.4 Session Resumed")

    # 3. Test Multi-Face Scan Endpoint with Empty/Synthetic Image
    test_img = create_synthetic_face_image()
    scan_res = client.post('/api/attendance/smart-face-scan', json={
        'image': test_img,
        'subject_id': test_subject_id,
        'session_id': session_id
    }, headers=auth_headers)
    assert scan_res.status_code == 200, f"Smart scan failed: {scan_res.data}"
    scan_data = scan_res.get_json()
    assert 'detected_count' in scan_data
    assert 'faces' in scan_data
    print(f"[PASS] 3. Multi-Face Scanning Endpoint Verified (Detected: {scan_data['detected_count']}, Faces Processed: {len(scan_data['faces'])})")

    # 4. End Session
    end_res = client.post(f'/api/sessions/{session_id}/end', headers=auth_headers)
    assert end_res.status_code == 200
    assert end_res.get_json()['session']['status'] == 'ENDED'
    print("[PASS] 4. Session Ended")

    # 5. Test Global AI Attendance Intelligence Endpoint
    intel_res = client.get('/api/analytics/intelligence', headers=auth_headers)
    assert intel_res.status_code == 200, f"Intelligence API failed: {intel_res.data}"
    intel_data = intel_res.get_json()
    assert 'summary' in intel_data
    assert 'students_intelligence' in intel_data
    assert 'at_risk_students' in intel_data
    assert 'daily_timeline' in intel_data
    print(f"[PASS] 5. Global AI Analytics & Intelligence (Average Rate: {intel_data['summary']['avg_attendance_rate']}%, At-Risk Students: {len(intel_data['at_risk_students'])}, Total Students: {intel_data['summary']['total_students']})")

    # 6. Test Individual Student AI Analytics & Predictive Formula
    stu_res = client.get('/api/students', headers=auth_headers)
    students = stu_res.get_json()
    if len(students) > 0:
        first_student = students[0]
        stu_id = first_student['id']
        stu_intel_res = client.get(f'/api/analytics/student/{stu_id}', headers=auth_headers)
        assert stu_intel_res.status_code == 200, f"Student intelligence failed: {stu_intel_res.data}"
        stu_intel = stu_intel_res.get_json()
        assert 'overall_metrics' in stu_intel
        assert 'subject_breakdown' in stu_intel
        om = stu_intel['overall_metrics']
        print(f"[PASS] 6. Individual Student AI Predictor (#STD-{stu_id}: Rate: {om['attendance_rate']}%, Risk: {om['risk_level']} {om['risk_label']}, Advice: {om['actionable_advice']})")

    # 7. Test Suspicious Activity Audit & Resolution
    audit_res = client.get('/api/audit/suspicious-activity', headers=auth_headers)
    assert audit_res.status_code == 200, f"Audit API failed: {audit_res.data}"
    audit_data = audit_res.get_json()
    print(f"[PASS] 7.1 Suspicious Activity Audit Feed (Total Alerts: {len(audit_data)})")

    if len(audit_data) > 0:
        incident_id = audit_data[0]['id']
        resolve_res = client.post(f'/api/audit/suspicious-activity/{incident_id}/resolve', headers=auth_headers)
        assert resolve_res.status_code == 200
        print(f"[PASS] 7.2 Incident Resolution Tested (#INC-{incident_id} marked RESOLVED)")

    # 8. Test Advanced Attendance Reports & Pagination
    reports_res = client.get('/api/reports/attendance-advanced?page=1&limit=10', headers=auth_headers)
    assert reports_res.status_code == 200, f"Advanced reports API failed: {reports_res.data}"
    reports_data = reports_res.get_json()
    assert 'data' in reports_data
    assert 'pagination' in reports_data
    print(f"[PASS] 8. Advanced Filtered Reports (Page: {reports_data['pagination']['page']}, Total Records: {reports_data['pagination']['total_records']})")

    print("\n==================================================")
    print("   ALL AI ATTENDANCE SUITE TESTS PASSED (100%)    ")
    print("==================================================")

if __name__ == '__main__':
    run_tests()
