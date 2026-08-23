# Attendify - Smart Facial Recognition Attendance Management System

Attendify is an enterprise-ready, contactless Attendance Management System powered by **Deep Learning Facial Biometrics** and **TiDB Cloud Serverless**. It features a modern dark-slate SaaS UI/UX, robust role-based access control (Admin, Faculty, Student), real-time biometric verification, manual roll call sheets, and analytics telemetry.

---

## Key Features

- **Contactless AI Facial Recognition**: 128-dimensional deep facial embeddings using `face_recognition` and `dlib` with real-time camera viewfinder HUD and audio feedback chimes.
- **TiDB Cloud Serverless**: Globally distributed, MySQL-compatible cloud database with encrypted TLS/SSL connections and connection pooling.
- **Role-Based Portals**:
  - **Admin**: Complete institutional control over students, faculty, courses, subjects, biometric registrations, and aggregate analytics.
  - **Faculty / Teacher**: Lecture schedule management, live roll calls (Face Recognition or Manual), and class reports.
  - **Student**: Personal attendance rate gauge, **75% Exam Eligibility compliance alert**, subject breakdown, and history logs.
- **Manual Roll Call Sheet**: Fast bulk attendance actions (*Mark All Present*, *Mark All Absent*) with instant TiDB synchronization.
- **Telemetry & Reports**: Multi-parameter filters (Subject, Date, Status) and instant CSV exports.

---

## Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Frontend** | HTML5, CSS3 (Custom SaaS Design System, Glassmorphism), Vanilla ES6+ JavaScript, Chart.js |
| **Backend** | Python 3.10+, Flask REST API, PyJWT, Bcrypt, PyMySQL with SSL (`certifi`) |
| **AI / Biometrics** | `face_recognition`, `dlib-bin`, `opencv-python-headless`, `Pillow`, `numpy` |
| **Database** | **TiDB Cloud Serverless** (MySQL 8.0 wire-compatible) |
| **Deployment** | Vercel (Frontend & Serverless) / Render, Railway, Docker (Persistent Vision Backend) |

---

## Local Setup & Development

### 1. Prerequisites
- Python 3.10 or higher
- Git
- Modern browser with webcam access (Chrome, Edge, Firefox, Safari)

### 2. Clone and Install Dependencies
```bash
git clone https://github.com/Prashant453/Attendance-Management-System.git
cd Attendance-Management-System

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `backend/.env` and update your database credentials:
```bash
cp .env.example backend/.env
```
Fill in your TiDB Cloud credentials:
```ini
DB_HOST=gateway01.ap-southeast-1.prod.aws.tidbcloud.com
DB_PORT=4000
DB_USER=your_tidb_username
DB_PASSWORD=your_tidb_password
DB_NAME=attendance_db
DB_SSL=true
SECRET_KEY=your_jwt_secret_key_here
PORT=5000
```

### 4. Run the Application
```bash
python backend/app.py
```
Open [http://localhost:5000](http://localhost:5000) in your browser.

---

## Default Demo Accounts

| Role | Username | Password | Access Scope |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin` | `admin123` | Institutional dashboard, student/course/teacher management, face enrollment, scanner, reports |
| **Teacher** | `prof_smith` | `teacher123` | Class schedule, face attendance terminal, manual roll call, reports |
| **Student** | `alex_j` | `student123` | Personal attendance statistics, 75% exam compliance banner, logs |

---

## Production Deployment Guide

### Architecture Overview
- **Frontend**: Deployed seamlessly on **Vercel** with global CDN caching.
- **Database**: Managed on **TiDB Cloud Serverless** with automated scaling.
- **Backend**: Can run directly on **Render**, **Railway**, **AWS/GCP EC2**, or **Docker** where native C++ libraries (`dlib`, OpenCV) execute facial recognition in real-time.

### Deploying Frontend to Vercel
1. Push this repository to GitHub.
2. Log into [Vercel](https://vercel.com) and click **Add New Project**.
3. Import your GitHub repository: `Prashant453/Attendance-Management-System`.
4. Set the **Root Directory** to `frontend` (or keep root with the included `vercel.json`).
5. (Optional) If your backend is hosted separately on Render or Railway, update `frontend/assets/js/config.js` with your backend URL:
   ```javascript
   window.ATTENDIFY_CONFIG = {
       API_BASE_URL: 'https://your-backend-api.onrender.com/api'
   };
   ```
6. Click **Deploy**.

### Deploying Backend to Render / Railway
1. Create a new **Web Service** on [Render](https://render.com) or [Railway](https://railway.app).
2. Connect your GitHub repository.
3. Configure settings:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn backend.app:app --chdir backend` (or `python backend/app.py`)
4. Add your Environment Variables (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `SECRET_KEY`).
5. Deploy the service and copy the public HTTPS URL into `frontend/assets/js/config.js`.

