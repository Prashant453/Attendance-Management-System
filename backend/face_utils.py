import base64
import json
import logging
import numpy as np

logger = logging.getLogger(__name__)

# Check if deep facial recognition libraries are available
FACE_RECOG_AVAILABLE = False
try:
    import cv2
    import face_recognition
    FACE_RECOG_AVAILABLE = True
    logger.info("Face recognition engine (dlib + OpenCV) initialized successfully.")
except ImportError as e:
    logger.warning(f"face_recognition or OpenCV not found: {e}. Running in fallback mode.")


def decode_image_base64(image_data):
    """
    Safely decode Base64 image strings with or without data URI prefixes.
    """
    try:
        import cv2
        if not image_data or not isinstance(image_data, str):
            return None
        
        # Strip header if present (e.g. data:image/jpeg;base64,...)
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]
        
        # Remove any whitespace or newline artifacts
        clean_base64 = "".join(image_data.split())
        decoded_bytes = base64.b64decode(clean_base64)
        nparr = np.frombuffer(decoded_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        logger.error(f"Image base64 decoding error: {e}")
        return None


def get_face_encodings(image_data, is_base64=True):
    """
    Extract 128-dimensional facial embedding vector using multi-stage detection.
    Stage 1: Standard HOG model
    Stage 2: Upsampled HOG model (for smaller/distant faces)
    """
    if not FACE_RECOG_AVAILABLE:
        logger.error("Face recognition engine is not installed.")
        return None
    
    try:
        import cv2
        import face_recognition

        if is_base64:
            img = decode_image_base64(image_data)
        else:
            img = image_data

        if img is None or img.size == 0:
            logger.warning("Decoded image is empty or invalid.")
            return None

        # Convert BGR to RGB
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # Stage 1: Standard HOG face detection
        face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=1)
        
        # Stage 2: Upsampled detection if not found
        if not face_locations:
            face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=2)

        if not face_locations:
            logger.warning("No face detected in the provided image frame.")
            return None

        # Extract 128-d face encodings
        face_encodings = face_recognition.face_encodings(rgb_img, known_face_locations=face_locations, num_jitters=1)
        
        if not face_encodings:
            return None

        # Return first face embedding as list of floats
        return [float(x) for x in face_encodings[0]]
    except Exception as e:
        logger.error(f"Face encoding extraction exception: {e}")
        return None


def compare_faces(known_encodings_dict, frame_base64, tolerance=0.52):
    """
    Match faces detected in camera frame against known student face encodings.
    known_encodings_dict: { student_id: [128 floats] }
    Returns: list of matched student_ids
    """
    if not FACE_RECOG_AVAILABLE:
        logger.error("Face recognition engine is not installed.")
        return []
    
    try:
        import cv2
        import face_recognition

        img = decode_image_base64(frame_base64)
        if img is None or img.size == 0:
            return []

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # Detect face locations in the live frame
        face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=1)
        if not face_locations:
            face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=2)

        if not face_locations:
            return []

        frame_encodings = face_recognition.face_encodings(rgb_img, known_face_locations=face_locations)
        if not frame_encodings:
            return []

        student_ids = list(known_encodings_dict.keys())
        known_encodings = [np.array(known_encodings_dict[sid], dtype=np.float64) for sid in student_ids]

        if not known_encodings:
            return []

        recognized_ids = []
        for face_encoding in frame_encodings:
            matches = face_recognition.compare_faces(known_encodings, face_encoding, tolerance=tolerance)
            face_distances = face_recognition.face_distance(known_encodings, face_encoding)
            
            best_match_index = int(np.argmin(face_distances))
            if matches[best_match_index] and face_distances[best_match_index] <= tolerance:
                matched_id = student_ids[best_match_index]
                logger.info(f"Student matched: ID {matched_id} (Distance: {face_distances[best_match_index]:.3f})")
                recognized_ids.append(matched_id)

        return list(set(recognized_ids))
    except Exception as e:
        logger.error(f"Face comparison exception: {e}")
        return []

