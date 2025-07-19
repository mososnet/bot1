import os
import tempfile
import subprocess
from threading import Thread

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler

from flask import Flask

# ---- Flask Webserver to keep port open ----
app_flask = Flask("webserver")

@app_flask.route("/")
def home():
    return "بوت التليجرام شغال. البورت مفتوح :)"

def run_flask():
    app_flask.run(host="0.0.0.0", port=8000)

# --------------------------------------------

def is_video_portrait(input_path):
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
    portrait = is_video_portrait(input_path)
    fps = 15 if duration <= 3 else 12 if duration <= 6 else 10
    fps = min(fps, 15)

    if portrait:
        filter_fg = f"[0:v]scale=iw*min({width}/iw\\,{height}/ih):ih*min({width}/iw\\,{height}/ih),setsar=1 [fg]"
        filter_bg = f"[0:v]scale={width}:{height},boxblur=luma_radius=10:luma_power=1,format=yuva420p,colorchannelmixer=aa=0.5 [bg]"
        overlay = f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={fps}"
        filter_chain = f"{filter_fg};{filter_bg};{overlay}"
    else:
        filter_chain = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,fps={fps}'

    palette_path = os.path.join(tempfile.gettempdir(), "palette.png")

    palette_cmd = [
        'ffmpeg', '-ss', str(start), '-t', str(duration),
        '-i', input_path,
        '-vf', filter_chain + ',palettegen',
        '-y', palette_path
    ]
    res1 = subprocess.run(palette_cmd, capture_output=True)
    if res1.returncode != 0:
        raise Exception(f"Palette generation failed: {res1.stderr.decode()}")

    gif_cmd = [
        'ffmpeg', '-ss', str(start), '-t', str(duration),
        '-i', input_path, '-i', palette_path,
        '-lavfi', filter_chain + '[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5',
        '-gifflags', '+transdiff',
        '-y', output_path
    ]
    res2 = subprocess.run(gif_cmd, capture_output=True)
    if res2.returncode != 0:
        raise Exception(f"GIF creation failed: {res2.stderr.decode()}")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
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
        await update.message.reply_text("اكتب /convert مع وقتين: البداية والنهاية بالثواني.\nمثال: /convert 3 7")
        return
    try:
        start_sec = float(args[0])
        end_sec = float(args[1])
        if start_sec < 0 or end_sec <= start_sec:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("تأكد أن القيم أرقام، والنهاية أكبر من البداية.")
        return

    context.user_data['start_sec'] = start_sec
    context.user_data['end_sec'] = end_sec
    await update.message.reply_text(f"تم ضبط وقت المقطع من {start_sec} إلى {end_sec} ثانية. أرسل الفيديو.")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    if not video:
        await update.message.reply_text("أرسل فيديو فقط.")
        return

    file = await context.bot.get_file(video.file_id)

    with tempfile.TemporaryDirectory() as tmpdirname:
        input_path = os.path.join(tmpdirname, "input.mp4")
        output_path = os.path.join(tmpdirname, "output.gif")
        await file.download_to_drive(input_path)

        start_sec = context.user_data.get('start_sec', 0)
        end_sec = context.user_data.get('end_sec', start_sec + 5)
        duration = max(end_sec - start_sec, 5)

        try:
            size_mb = convert_video_to_gif_ffmpeg(input_path, output_path, start=start_sec, duration=duration)
            with open(output_path, 'rb') as gif_file:
                await update.message.reply_animation(gif_file, caption=f"✅ تم التحويل بنجاح ({size_mb:.2f} MB)")
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطأ: {e}")

        context.user_data.clear()

if __name__ == '__main__':
    BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")

    Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("convert", convert_command))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    print("✅ البوت شغّال الآن... مع بورت ويب مفتوح على 8000")
    app.run_polling()
