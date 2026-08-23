/**
 * Enhanced Camera Manager for Facial Recognition
 */
class CameraHandler {
    constructor(videoElement, canvasElement = null) {
        this.video = videoElement;
        this.canvas = canvasElement || document.createElement('canvas');
        this.stream = null;
        this.selectedDeviceId = null;
    }

    async getDevices() {
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            return devices.filter(device => device.kind === 'videoinput');
        } catch (err) {
            console.warn("Cannot enumerate media devices:", err);
            return [];
        }
    }

    async start(deviceId = null) {
        this.stop();
        this.selectedDeviceId = deviceId;

        const constraints = {
            video: deviceId 
                ? { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
                : { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
            audio: false
        };

        try {
            this.stream = await navigator.mediaDevices.getUserMedia(constraints);
            this.video.srcObject = this.stream;
            await this.video.play();
            return { success: true };
        } catch (err) {
            console.error("Camera access error:", err);
            let message = 'Unable to access camera.';
            if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
                message = 'Camera permission was denied. Please allow camera access in your browser settings.';
            } else if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
                message = 'No camera device detected on your system.';
            } else if (err.name === 'NotReadableError' || err.name === 'TrackStartError') {
                message = 'Camera is in use by another application.';
            }
            return { success: false, error: message };
        }
    }

    stop() {
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }
        if (this.video) {
            this.video.srcObject = null;
        }
    }

    isActive() {
        return !!(this.stream && this.stream.active);
    }

    captureFrame(quality = 0.85) {
        if (!this.video || this.video.videoWidth === 0) {
            return null;
        }
        const context = this.canvas.getContext('2d');
        this.canvas.width = this.video.videoWidth;
        this.canvas.height = this.video.videoHeight;
        context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
        return this.canvas.toDataURL('image/jpeg', quality);
    }
}

let mainCamera = null;

