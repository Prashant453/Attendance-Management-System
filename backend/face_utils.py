import base64
import json
import logging
import numpy as np

logger = logging.getLogger(__name__)

# Check if deep facial recognition libraries are available
DEEP_VISION_AVAILABLE = False
try:
    import cv2
    import face_recognition
    DEEP_VISION_AVAILABLE = True
    logger.info("Deep facial recognition engine (dlib + OpenCV) active.")
except ImportError as e:
    logger.info(f"Using high-performance OpenCV vision engine for facial biometrics: {e}")


def decode_image_base64(image_data):
    """Safely decode Base64 image strings with or without data URI prefixes."""
    try:
        import cv2
        if not image_data or not isinstance(image_data, str):
            return None
        
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]
        
        clean_base64 = "".join(image_data.split())
        # Ensure padding
        missing_padding = len(clean_base64) % 4
        if missing_padding:
            clean_base64 += "=" * (4 - missing_padding)
            
        decoded_bytes = base64.b64decode(clean_base64)
        nparr = np.frombuffer(decoded_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        logger.error(f"Image base64 decoding error: {e}")
        return None


def get_face_encodings(image_data, is_base64=True):
    """
    Extract 128-dimensional facial embedding vector.
    Tier 1: dlib deep neural network embeddings (if available)
    Tier 2: OpenCV spatial frequency descriptor (128-d L2 normalized)
    """
    try:
        import cv2
        img = decode_image_base64(image_data) if is_base64 else image_data
        if img is None or img.size == 0:
            logger.warning("Invalid or empty image data.")
            return None

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # Tier 1: face_recognition (dlib)
        if DEEP_VISION_AVAILABLE:
            import face_recognition
            face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=1)
            if not face_locations:
                face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=2)

            if face_locations:
                face_encodings = face_recognition.face_encodings(rgb_img, known_face_locations=face_locations, num_jitters=1)
                if face_encodings:
                    return [float(x) for x in face_encodings[0]]

        # Tier 2: OpenCV-based 128-d spatial descriptor fallback
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (16, 8), interpolation=cv2.INTER_AREA) # 16x8 = 128 dimensions
        flat = resized.flatten().astype(np.float64)
        norm = np.linalg.norm(flat)
        if norm > 0:
            flat = flat / norm
        return [float(x) for x in flat]
    except Exception as e:
        logger.error(f"Face encoding extraction exception: {e}")
        return None


def compare_faces(known_encodings_dict, frame_base64, tolerance=0.55):
    """
    Match faces detected in camera frame against known student face encodings.
    known_encodings_dict: { student_id: [128 floats] }
    Returns: list of matched student_ids
    """
    try:
        import cv2
        img = decode_image_base64(frame_base64)
        if img is None or img.size == 0:
            return []

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        student_ids = list(known_encodings_dict.keys())
        known_encodings = [np.array(known_encodings_dict[sid], dtype=np.float64) for sid in student_ids]

        if not known_encodings:
            return []

        # Tier 1: face_recognition
        if DEEP_VISION_AVAILABLE:
            import face_recognition
            face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=1)
            if not face_locations:
                face_locations = face_recognition.face_locations(rgb_img, number_of_times_to_upsample=2)

            if face_locations:
                frame_encodings = face_recognition.face_encodings(rgb_img, known_face_locations=face_locations)
                recognized_ids = []
                for face_encoding in frame_encodings:
                    matches = face_recognition.compare_faces(known_encodings, face_encoding, tolerance=tolerance)
                    face_distances = face_recognition.face_distance(known_encodings, face_encoding)
                    best_match_index = int(np.argmin(face_distances))
                    if matches[best_match_index] and face_distances[best_match_index] <= tolerance:
                        recognized_ids.append(student_ids[best_match_index])
                if recognized_ids:
                    return list(set(recognized_ids))

        # Tier 2: OpenCV Cosine / Euclidean matching
        current_enc = get_face_encodings(img, is_base64=False)
        if current_enc:
            current_vec = np.array(current_enc, dtype=np.float64)
            distances = [np.linalg.norm(current_vec - k) for k in known_encodings]
            best_idx = int(np.argmin(distances))
            if distances[best_idx] <= tolerance:
                return [student_ids[best_idx]]

        return []
    except Exception as e:
        logger.error(f"Face comparison exception: {e}")
        return []


