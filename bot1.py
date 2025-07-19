import os
import tempfile
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler

UPLOAD_FOLDER = 'static/gifs'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def is_video_portrait(input_path):
    """يرجع True إذا الفيديو طولي (ارتفاع أكبر من العرض)"""
    cmd = [
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'csv=s=x:p=0', input_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False
    try:
        width, height = map(int, result.stdout.strip().split('x'))
        return height > width
    except Exception:
        return False

def convert_video_to_gif_ffmpeg(input_path, output_path, width=320, height=320, start=0, duration=5, max_size_mb=2.45):
    # تحديد fps حسب مدة الفيديو (تعديل تلقائي لتقليل الحجم)
    if duration <= 3:
        fps = 15
    elif duration <= 6:
        fps = 12
    else:
        fps = 10
    fps = min(fps, 15)

    portrait = is_video_portrait(input_path)

    base_filter = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x00000000'

    if portrait:
        filter_chain = f'{base_filter},hflip,fps={fps}'
    else:
        filter_chain = f'{base_filter},fps={fps}'

    palette_path = os.path.join(tempfile.gettempdir(), "palette.png")

    # إنشاء لوحة ألوان
    palette_cmd = [
        'ffmpeg',
        '-ss', str(start),
        '-t', str(duration),
        '-i', input_path,
        '-vf', filter_chain + ',palettegen',
        '-y',
        palette_path
    ]
    result = subprocess.run(palette_cmd, capture_output=True)
    if result.returncode != 0:
        raise Exception(f"ffmpeg palettegen error: {result.stderr.decode()}")

    # صنع الـ GIF باستخدام اللوحة
    gif_cmd = [
        'ffmpeg',
        '-ss', str(start),
        '-t', str(duration),
        '-i', input_path,
        '-i', palette_path,
        '-lavfi', filter_chain + '[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5',
        '-gifflags', '+transdiff',
        '-y',
        output_path
    ]
    result = subprocess.run(gif_cmd, capture_output=True)
    if result.returncode != 0:
        raise Exception(f"ffmpeg gif creation error: {result.stderr.decode()}")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    attempt = 0
    while size_mb > max_size_mb and fps > 5 and attempt < 3:
        fps -= 2
        if portrait:
            filter_chain = f'{base_filter},hflip,fps={fps}'
        else:
            filter_chain = f'{base_filter},fps={fps}'

        palette_cmd = [
            'ffmpeg',
            '-ss', str(start),
            '-t', str(duration),
            '-i', input_path,
            '-vf', filter_chain + ',palettegen',
            '-y',
            palette_path
        ]
        result = subprocess.run(palette_cmd, capture_output=True)
        if result.returncode != 0:
            raise Exception(f"ffmpeg palettegen error: {result.stderr.decode()}")

        gif_cmd = [
            'ffmpeg',
            '-ss', str(start),
            '-t', str(duration),
            '-i', input_path,
            '-i', palette_path,
            '-lavfi', filter_chain + '[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5',
            '-gifflags', '+transdiff',
            '-y',
            output_path
        ]
        result = subprocess.run(gif_cmd, capture_output=True)
        if result.returncode != 0:
            raise Exception(f"ffmpeg gif creation error: {result.stderr.decode()}")

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        attempt += 1

    return size_mb

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحبًا! \n"
        "ارسل لي فيديو لتحويله إلى GIF بحجم 320x320.\n"
        "تقدر تستخدم الأمر /convert لكتابة وقت البداية والنهاية بالثواني.\n"
        "مثال: /convert 3 7 (يحول الفيديو من الثانية 3 إلى 7)\n"
        "إذا ما حددت أوقات، سيأخذ أول 5 ثواني."
    )

async def convert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("الرجاء ارسال أمر /convert مع رقمين: وقت البداية ووقت النهاية بالثواني.\nمثال: /convert 3 7")
        return
    try:
        start_sec = float(args[0])
        end_sec = float(args[1])
        if start_sec < 0 or end_sec <= start_sec:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("الرجاء التأكد من أن الوقت هو رقم صحيح أو عشري، وأن النهاية أكبر من البداية.")
        return

    context.user_data['start_sec'] = start_sec
    context.user_data['end_sec'] = end_sec
    await update.message.reply_text(f"تم ضبط وقت المقطع من {start_sec} إلى {end_sec} ثانية. أرسل الفيديو الآن.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    if not video:
        await update.message.reply_text("أرسل فيديو فقط.")
        return

    file_id = video.file_id
    file = await context.bot.get_file(file_id)

    with tempfile.TemporaryDirectory() as tmpdirname:
        input_path = os.path.join(tmpdirname, "input.mp4")
        output_path = os.path.join(tmpdirname, "output.gif")

        await file.download_to_drive(input_path)

        start_sec = context.user_data.get('start_sec', 0)
        end_sec = context.user_data.get('end_sec', start_sec + 5)
        duration_sec = end_sec - start_sec
        if duration_sec <= 0:
            duration_sec = 5

        try:
            size_mb = convert_video_to_gif_ffmpeg(input_path, output_path, width=320, height=320,
                                                  start=start_sec, duration=duration_sec, max_size_mb=2.45)
            with open(output_path, 'rb') as gif_file:
                await update.message.reply_animation(gif_file, caption=f"✅ تم الإنشاء بحجم {size_mb:.2f} ميجابايت.")
        except Exception as e:
            await update.message.reply_text(f"حدث خطأ أثناء التحويل: {e}")

        context.user_data.pop('start_sec', None)
        context.user_data.pop('end_sec', None)

if __name__ == '__main__':
    BOT_TOKEN = "7211726528:AAGmVatPqvK-2SmlvZqKmKSGei5gNbRyMCM"

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("convert", convert_command))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    print("البوت شغّال...")
    app.run_polling()
