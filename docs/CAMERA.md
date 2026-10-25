# CoFlorist camera sources

The installed LeLab source inspected for this integration is:
`C:/Users/0120c/Downloads/lelab-omx-main/lelab-omx-main`.

LeLab manages camera selection and recording configuration. Its live preview is a browser MediaStream, not a video stream served by its Python HTTP API:

- `frontend/src/hooks/useCameraStream.ts`: getUserMedia(deviceId), video.srcObject, release on pause/unmount.
- `frontend/src/hooks/useAvailableCameras.ts`: matches browser MediaDeviceInfo labels to server camera names.
- `lelab/server.py:1091`: GET /available-cameras returns names and indices, not video frames.
- `lelab/record.py`: OpenCV cameras are opened for recording after browser previews release the device.
- `/ws/joint-data` carries joint/event data, not camera images.

CoFlorist Settings → 작업 공간 supports:

1. **연결된 카메라**: choose the USB camera directly. This is the same capture approach used by LeLab and D4D. If LeLab recording/inference owns the device exclusively, use a separate camera or release it in the original app when appropriate.
2. **영상 URL**: an actual MJPEG/image stream or browser-playable video URL, not the LeLab home page. No motor-control proxy is installed.
3. **LeLab 화면 공유**: choose the already open LeLab tab/window in the browser's sharing picker. This shares the selected screen content into CoFlorist without opening the USB camera a second time. It is not a native LeLab video endpoint. Sharing must be selected again after a page reload; ending sharing releases only CoFlorist's stream.

Camera pictures are displayed locally; they are not sent to Nebius or analyzed by the language model. Reference photos remain labelled until a real camera or screen stream is connected. Screen sharing may include other visible content in the selected window, so choose the LeLab tab/window that should appear in the demo.

On inspection, `http://127.0.0.1:8000/health` refused the connection; the clone's presence alone does not mean its server is running. CoFlorist does not start, stop or alter the existing robot-control process.
