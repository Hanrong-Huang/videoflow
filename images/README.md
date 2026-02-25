# Reference Images

Place reference images here to use as starting frames for video generation.

## Supported Formats
- `.png`, `.jpg`, `.jpeg`, `.webp`

## Usage
When running `video_gen.py` in interactive mode, you'll be asked:
```
Attach a reference image? (image-to-video)
```
Select an image from this folder or enter a URL directly.

The image serves as the **first frame** of the generated video — the AI model
animates it based on your prompt.

## Notes
- Local images are **uploaded as base64** to the API (no external hosting needed).
- Keep images under **10 MB** for best results.
- Aspect ratio of the image should roughly match your chosen video aspect ratio.
