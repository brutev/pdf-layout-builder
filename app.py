#!/usr/bin/env python3
"""Run the document review UI and processing pipeline on localhost."""

import argparse
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image


ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_UPLOADS = {"image/jpeg": ".jpg", "image/png": ".png"}
ROOT_ASSETS = {
    "/": "app-index.html",
    "/app.css": "app.css",
    "/app.js": "app.js",
    "/sample": "index.html",
    "/viewer.css": "viewer.css",
    "/viewer.js": "viewer.js",
    "/viewer-data.js": "viewer-data.js",
    "/viewer-page.jpg": "viewer-page.jpg",
    "/new_policy_roi.pdf": "new_policy_roi.pdf",
    "/new_policy_roi.page-001.json": "new_policy_roi.page-001.json",
    "/new_policy_data_enhanced.json": "new_policy_data_enhanced.json",
}
REVIEW_ASSETS = {
    "index.html", "viewer.css", "viewer.js", "viewer-data.js", "viewer-page.jpg",
    "layout.pdf", "layout.page-001.json", "extracted.json", "enhanced.json",
    "classified.json",
}
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()


def set_job(job_id: str, **changes) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(changes)


def run_step(job_id: str, stage: str, args: list[str], log: Path) -> None:
    set_job(job_id, stage=stage)
    with log.open("a", encoding="utf-8") as stream:
        result = subprocess.run(
            [sys.executable, *args], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
            timeout=1200, check=False,
        )
    if result.returncode:
        raise RuntimeError(f"{stage} failed. See {log.name} in the job folder.")


def process_job(job_id: str, source: Path) -> None:
    folder = RUNS / job_id
    log = folder / "processing.log"
    with PROCESS_LOCK:
        try:
            run_step(job_id, "Detecting layout", [str(ROOT / "draw_layers.py"), "--input", str(source), "--output", str(folder / "layout.pdf")], log)
            run_step(job_id, "Extracting text", [str(ROOT / "extract_data.py"), "--input", str(source), "--output", str(folder / "extracted.json")], log)
            run_step(job_id, "Reading OCR lines", [str(ROOT / "enhance_rapidocr.py"), "--input", str(source), "--json", str(folder / "extracted.json"), "--output", str(folder / "enhanced.json"), "--no-table-cells"], log)

            set_job(job_id, stage="Classifying document")
            with log.open("a", encoding="utf-8") as stream:
                classification = subprocess.run(
                    [sys.executable, str(ROOT / "classify_document.py"), "--input", str(folder / "enhanced.json"), "--output", str(folder / "classified.json")],
                    cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, timeout=1200, check=False,
                )
            if classification.returncode:
                archive = json.loads((folder / "enhanced.json").read_text(encoding="utf-8"))
                archive["classification"] = {
                    "status": "model_unavailable",
                    "decision": "needs_review",
                    "model": "cross-encoder/nli-distilroberta-base",
                    "note": "RoBERTa classification did not complete. OCR review is available; see processing.log in the job folder.",
                }
                (folder / "classified.json").write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            run_step(job_id, "Preparing review", [
                str(ROOT / "build_viewer_data.py"), "--image", str(source),
                "--pdf", str(folder / "layout.pdf"),
                "--layout", str(folder / "layout.page-001.json"),
                "--ocr", str(folder / "classified.json"),
                "--output-dir", str(folder), "--title", "Uploaded document",
            ], log)
            for asset in ("index.html", "viewer.css", "viewer.js"):
                shutil.copy2(ROOT / asset, folder / asset)
            set_job(job_id, status="complete", stage="Review ready", review_url=f"/reviews/{job_id}/index.html")
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
            set_job(job_id, status="error", stage="Processing stopped", error=str(error))


class Handler(BaseHTTPRequestHandler):
    def respond_json(self, status: int, payload: dict) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def serve_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route.startswith("/api/jobs/"):
            job_id = route.removeprefix("/api/jobs/")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = dict(job) if job else None
            if payload is None:
                self.respond_json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
            else:
                self.respond_json(HTTPStatus.OK, payload)
            return
        if route.startswith("/reviews/"):
            parts = route.strip("/").split("/")
            if len(parts) != 3 or not re.fullmatch(r"[0-9a-f]{12}", parts[1]) or parts[2] not in REVIEW_ASSETS:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.serve_file(RUNS / parts[1] / parts[2])
            return
        asset = ROOT_ASSETS.get(route)
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.serve_file(ROOT / asset)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/jobs":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        suffix = ALLOWED_UPLOADS.get(content_type)
        if not suffix:
            self.respond_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Upload a JPEG or PNG image."})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.respond_json(HTTPStatus.LENGTH_REQUIRED, {"error": "File size is required."})
            return
        if not 0 < length <= MAX_UPLOAD_BYTES:
            self.respond_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Image must be under 20 MB."})
            return
        content = self.rfile.read(length)
        job_id = uuid.uuid4().hex[:12]
        folder = RUNS / job_id
        folder.mkdir(parents=True, exist_ok=False)
        source = folder / f"source{suffix}"
        source.write_bytes(content)
        try:
            with Image.open(source) as image:
                image.verify()
        except (OSError, ValueError):
            source.unlink(missing_ok=True)
            folder.rmdir()
            self.respond_json(HTTPStatus.BAD_REQUEST, {"error": "The image could not be read."})
            return
        with JOBS_LOCK:
            JOBS[job_id] = {"id": job_id, "status": "running", "stage": "Queued"}
        threading.Thread(target=process_job, args=(job_id, source), daemon=True).start()
        self.respond_json(HTTPStatus.ACCEPTED, JOBS[job_id])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the app in the default browser after the server starts")
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except PermissionError:
        parser.exit(2, "Could not open a localhost port in this environment. Run app.py from a local terminal with socket access.\n")
    address = f"http://127.0.0.1:{args.port}"
    print(f"Review app running at {address}", flush=True)
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
