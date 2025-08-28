import os
import sys
import subprocess
import time
import threading
from datetime import datetime
from yt_dlp import YoutubeDL
from collections import Counter

if getattr(sys, 'frozen', False):
    RAW_PATH = os.path.dirname(sys.executable)
else:
    RAW_PATH = os.path.dirname(os.path.abspath(__file__))

LAST_SPEED_FILE = os.path.join(RAW_PATH, "last_speed.txt")

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

FFMPEG_PATH = resource_path(os.path.join('bin', 'ffmpeg.exe'))
FFPROBE_PATH = resource_path(os.path.join('bin', 'ffprobe.exe'))

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

MAX_SPEEDS = 10

def save_last_speed(speed_bps):
    speeds = []
    try:
        if os.path.exists(LAST_SPEED_FILE):
            with open(LAST_SPEED_FILE, "r") as f:
                speeds = [int(line.strip()) for line in f if line.strip().isdigit()]
    except Exception as e:
        print(f"Failed to load speeds for saving: {e}")
    speeds.append(int(speed_bps))
    speeds = speeds[-MAX_SPEEDS:]
    try:
        with open(LAST_SPEED_FILE, "w") as f:
            for s in speeds:
                f.write(f"{s}\n")
    except Exception as e:
        print(f"Failed to save speeds: {e}")

def bin_speed_dynamic(speed, avg_speed):
    min_bin = 50_000
    bin_size = max(min_bin, int(avg_speed * 0.05))
    return (speed + bin_size // 2) // bin_size * bin_size

def load_last_speed():
    try:
        if not os.path.exists(LAST_SPEED_FILE):
            return None
        with open(LAST_SPEED_FILE, "r") as f:
            speeds = [int(line.strip()) for line in f if line.strip().isdigit()]
        if not speeds:
            return None
        avg_speed = sum(speeds) / len(speeds)
        binned_speeds = [bin_speed_dynamic(s, avg_speed) for s in speeds]
        counter = Counter(binned_speeds)
        most_common_binned_speed, count = counter.most_common(1)[0]
        return most_common_binned_speed
    except Exception as e:
        print(f"Failed to load speeds: {e}")
        return None

def sizeof_fmt(num, suffix='B'):
    if num is None:
        return "N/A"
    for unit in ['','K','M','G','T','P']:
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Y{suffix}"

def estimate_size(format_info, duration_sec):
    if format_info.get('filesize') is not None:
        return format_info['filesize']
    if format_info.get('filesize_approx') is not None:
        return format_info['filesize_approx']
    tbr = format_info.get('tbr')
    if tbr and duration_sec:
        return int(duration_sec * tbr * 1000 / 8)
    return None

def pick_video_format(formats, duration_sec, download_speed_bps):
    targets = [('4320', '   8K'), ('2160', '   4K'), ('1440', '1440p'), ('1080', '1080p')]
    best_formats = {res: None for res, _ in targets}
    size_estimates = {res: [] for res, _ in targets}

    for f in formats:
        if f.get('vcodec') == 'none':
            continue
        h = str(f.get('height'))
        fps = f.get('fps') or 0
        fmt_id = f.get('format_id')
        if h in best_formats:
            est_size = estimate_size(f, duration_sec)
            if est_size is not None:
                size_estimates[h].append(est_size)
            bf = best_formats[h]
            if bf is None or fps > bf['fps']:
                best_formats[h] = {
                    'format_id': fmt_id,
                    'fps': fps,
                    'ext': f.get('ext', ''),
                    'filesize': est_size,
                    'vcodec': f.get('vcodec'),
                }

    for res in size_estimates:
        if size_estimates[res]:
            avg_size = sum(size_estimates[res]) / len(size_estimates[res])
            if best_formats[res]:
                best_formats[res]['filesize'] = avg_size

    print("\nSelect video resolution:")
    for i, (res, name) in enumerate(targets, 1):
        fmt = best_formats[res]
        if fmt:
            size_bytes = fmt['filesize']
            size_str = sizeof_fmt(size_bytes)
            if size_bytes and download_speed_bps:
                est_sec_s = (size_bytes / download_speed_bps) * 1.5
                est_sec_f = (size_bytes / download_speed_bps) / 1.5
                est_min_s = int(est_sec_s // 60)
                est_min_f = int(est_sec_f // 60)
                est_sec_remainder_s = int(est_sec_s % 60)
                est_sec_remainder_f = int(est_sec_f % 60)
                est_time_str_s = f"{est_min_s}m {est_sec_remainder_s}s"
                est_time_str_f = f"{est_min_f}m {est_sec_remainder_f}s"
            else:
                est_time_str_s = est_time_str_f = "N/A"
            print(f"  {i}. {name}{fmt['fps']:.0f} | {size_str} | {est_time_str_s} - {est_time_str_f})")
        else:
            print(f"  {i}. {name}   | not available")

    while True:
        choice = input("Enter resolution choice (1-4): ").strip()
        if choice in {'1','2','3','4'}:
            chosen_res = targets[int(choice)-1][0]
            if best_formats[chosen_res]:
                return best_formats[chosen_res]
            else:
                print("That resolution is not available, please choose again.")
        else:
            print("Invalid choice, please enter 1-4.")

def get_video_formats(url):
    ydl_opts = {'quiet': True, 'no_warnings': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info

def gpu_supports_nvenc_nvdec():
    nvenc = nvdec = False
    try:
        out = subprocess.check_output([FFMPEG_PATH, '-hide_banner', '-encoders'], text=True)
        nvenc = any('nvenc' in line for line in out.splitlines())
        out = subprocess.check_output([FFMPEG_PATH, '-hide_banner', '-decoders'], text=True)
        nvdec = any('nvdec' in line or 'cuvid' in line for line in out.splitlines())
    except Exception as e:
        print(f"Failed to detect GPU support: {e}")
    return nvenc, nvdec

def merge_video_audio(video_path, audio_path, output_path):
    nvenc, _ = gpu_supports_nvenc_nvdec()

    # Probe video codec
    try:
        codec_name = subprocess.check_output([FFPROBE_PATH, '-v', 'error', '-select_streams', 'v:0',
                                             '-show_entries', 'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1',
                                             video_path], text=True).strip()
    except Exception:
        codec_name = None

    # If GPU-friendly, remux
    if nvenc and codec_name in ('h264','hevc'):
        print("GPU-friendly codec detected, remuxing without re-encode...")
        subprocess.run([FFMPEG_PATH, '-y', '-i', video_path, '-i', audio_path, '-c', 'copy', output_path], check=True)
        return

    # Ask user to re-encode or abort
    if nvenc:
        choice = input("Video codec not GPU-friendly. Re-encode with GPU? (y) or try another format? (n): ").strip().lower()
        if choice != 'y':
            print("Aborting merge. Please select another format with GPU-friendly codec.")
            return
        video_codec = 'h264_nvenc'
    else:
        print("GPU encoding not available, using CPU...")
        video_codec = 'libx264'

    print(f"Merging video + audio using {'GPU' if nvenc else 'CPU'} encoding...")

    # Thread to print ETA
    stop_thread = False
    total_size_est = os.path.getsize(video_path) + os.path.getsize(audio_path)
    start = time.time()

    def eta_thread():
        while not stop_thread:
            if os.path.exists(output_path):
                current_size = os.path.getsize(output_path)
                speed = current_size / max(time.time() - start, 0.1)
                remaining_bytes = max(total_size_est - current_size, 0)
                eta_sec = remaining_bytes / max(speed, 0.1)
                eta_min = int(eta_sec // 60)
                eta_sec_r = int(eta_sec % 60)
                print(f"\rRe-encoding ETA: {eta_min}m {eta_sec_r}s", end='', flush=True)
            time.sleep(1)

    t = threading.Thread(target=eta_thread)
    t.start()

    cmd = [FFMPEG_PATH, '-y', '-i', video_path, '-i', audio_path,
           '-c:v', video_codec, '-preset', 'p1', '-c:a', 'aac', output_path]
    subprocess.run(cmd, check=True)

    stop_thread = True
    t.join()
    print("\nMerge completed.")

def download_video_audio(url, video_fmt, do_audio, do_join, target_dir, video_only=False):
    os.makedirs(target_dir, exist_ok=True)

    # Extract video info for title
    with YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        video_title = info_dict.get('title', 'video').replace('/', '_').replace('\\', '_')

    if do_join and do_audio and not video_only:
        temp_video = os.path.join(target_dir, 'temp_video.mp4')
        temp_audio = os.path.join(target_dir, 'temp_audio.m4a')
        final_output = os.path.join(target_dir, f"{video_title}.mp4")

        # Download video
        ydl_opts_video = {'format': video_fmt['format_id'], 'outtmpl': temp_video, 'quiet': False, 'no_warnings': True}
        with YoutubeDL(ydl_opts_video) as ydl:
            ydl.download([url])

        # Download audio
        ydl_opts_audio = {'format': 'bestaudio[ext=m4a]/bestaudio', 'outtmpl': temp_audio, 'quiet': False, 'no_warnings': True}
        with YoutubeDL(ydl_opts_audio) as ydl:
            ydl.download([url])

        merge_video_audio(temp_video, temp_audio, final_output)
        os.remove(temp_video)
        os.remove(temp_audio)

    elif video_only:
        outtmpl = os.path.join(target_dir, f"{video_title}.%(ext)s")
        ydl_opts = {'format': video_fmt['format_id'], 'outtmpl': outtmpl, 'quiet': False, 'no_warnings': True}
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    elif do_audio and not video_only:
        temp_audio = os.path.join(target_dir, f"{video_title}.m4a")
        wav_output = os.path.join(target_dir, f"{video_title}.wav")  # <-- new wav output

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_audio,
            'quiet': False,
            'no_warnings': True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Convert to WAV (96kHz) for Resolve
        print(f"Converting {temp_audio} → {wav_output} (96kHz WAV)...")
        subprocess.run([FFMPEG_PATH, '-y', '-i', temp_audio, '-ar', '96000', '-sample_fmt', 's24le', wav_output], check=True)
        os.remove(temp_audio)
        print("Conversion complete.")

def rename_folder(target_dir):
    files = os.listdir(target_dir)
    candidates = [f for f in files if f.lower().endswith('.mp4')] or [f for f in files if f.lower().endswith('.wav')]
    if not candidates:
        print("No mp4 or wav output found for renaming.")
        return
    base_name = os.path.splitext(candidates[0])[0]
    parent_dir = os.path.dirname(target_dir)
    new_folder_path = os.path.join(parent_dir, base_name)
    if os.path.exists(new_folder_path):
        print(f"Target folder name '{base_name}' already exists, skipping rename.")
        return
    os.rename(target_dir, new_folder_path)
    print(f"Renamed folder to: {new_folder_path}")

def main():

    url = input("Paste YouTube URL: ").strip()
    
    choice = None
    while choice not in {'1','2','3','4'}:
        clear_console()
        print("Select download mode:\n")
        print("  1. Video + Audio (joined)")
        print("  2. Video + Audio (separate)")
        print("  3. Video only")
        print("  4. Audio only\n")
        choice = input("Enter choice: ").strip()

    do_video = choice in {'1', '2', '3'}
    do_audio = choice in {'1', '2', '4'}
    do_join = choice == '1'
    video_only = choice == '3'

    download_speed_bps = load_last_speed()
    if download_speed_bps is None:
        print("No saved download speed found, showing estimates as N/A")

    info = None
    video_fmt = None
    duration = None
    if do_video:
        info = get_video_formats(url)
        duration = info.get('duration')
        video_fmt = pick_video_format(info['formats'], duration, download_speed_bps)

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    target_dir = os.path.join(RAW_PATH, timestamp)

    download_video_audio(url, video_fmt, do_audio, do_join, target_dir, video_only=video_only)
    rename_folder(target_dir)

if __name__ == "__main__":
    main()