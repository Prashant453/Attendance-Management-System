/**
 * ATTENDIFY RUNTIME CONFIGURATION
 * Allows seamless switching between local Flask backend and production cloud deployment.
 */
window.ATTENDIFY_CONFIG = {
    // If backend is deployed on a separate domain (e.g. Render / Railway / Docker), specify it here:
    // API_BASE_URL: 'https://your-backend.onrender.com/api',
    
    // Left undefined by default so api.js resolves dynamically:
    API_BASE_URL: 'https://attendify-backends.onrender.com',
    APP_NAME: 'Attendify',
    VERSION: '2.0.0-production'
};
