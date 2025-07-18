import asyncio
import threading
import schedule
import time
import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import datetime
import json
import queue

LAST_IDS_FILE = "last_ids.json"

class TelegramBackupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Backup App")

        tk.Label(root, text="API ID:").grid(row=0, column=0, sticky="e")
        tk.Label(root, text="API Hash:").grid(row=1, column=0, sticky="e")
        tk.Label(root, text="Phone Number:").grid(row=2, column=0, sticky="e")
        tk.Label(root, text="Select Chats:").grid(row=3, column=0, sticky="ne")

        self.api_id_entry = tk.Entry(root)
        self.api_hash_entry = tk.Entry(root)
        self.phone_entry = tk.Entry(root)

        self.api_id_entry.grid(row=0, column=1)
        self.api_hash_entry.grid(row=1, column=1)
        self.phone_entry.grid(row=2, column=1)

        # Multiple selection Listbox
        self.chat_listbox = tk.Listbox(root, selectmode=tk.MULTIPLE, height=10, exportselection=False)
        self.chat_listbox.grid(row=3, column=1, sticky="ew")

        self.status_text = tk.Text(root, height=15, width=70)
        self.status_text.grid(row=5, column=0, columnspan=3, pady=10)

        self.login_button = tk.Button(root, text="Login & Load Chats", command=self.login)
        self.start_button = tk.Button(root, text="Start Daily Backup", command=self.start_scheduler, state="disabled")
        self.stop_button = tk.Button(root, text="Stop Scheduler", command=self.stop_scheduler, state="disabled")

        self.login_button.grid(row=4, column=0, pady=5)
        self.start_button.grid(row=4, column=1)
        self.stop_button.grid(row=4, column=2)

        self.client = None
        self.scheduler_thread = None
        self.running = False
        self.loop = asyncio.new_event_loop()

        self.loop_thread = threading.Thread(target=self.run_loop, daemon=True)
        self.loop_thread.start()

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def log(self, message):
        self.status_text.insert(tk.END, message + "\n")
        self.status_text.see(tk.END)

    def prompt_user_input(self, prompt, title="Input", show=None):
        result_queue = queue.Queue()

        def ask():
            result = simpledialog.askstring(title, prompt, show=show)
            result_queue.put(result)

        self.root.after(0, ask)
        return result_queue.get()

    def login(self):
        api_id = self.api_id_entry.get()
        api_hash = self.api_hash_entry.get()
        phone = self.phone_entry.get()
        if not api_id or not api_hash or not phone:
            messagebox.showerror("Error", "Please fill in all API credentials and phone.")
            return
        try:
            api_id = int(api_id)
        except ValueError:
            messagebox.showerror("Error", "API ID must be a number.")
            return

        self.client = TelegramClient('telegram_backup_session', api_id, api_hash, loop=self.loop)
        asyncio.run_coroutine_threadsafe(self._login(phone), self.loop)

    async def _login(self, phone):
        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.log("Sending code request...")
                await self.client.send_code_request(phone)
                self.log("Code sent to Telegram. Please enter it.")

                code = await self.loop.run_in_executor(None, lambda: self.prompt_user_input("Enter the login code:", "Telegram Login"))
                if not code:
                    self.log("Login cancelled.")
                    return

                try:
                    await self.client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    password = await self.loop.run_in_executor(None, lambda: self.prompt_user_input("Enter your 2FA password:", "2FA Password", show="*"))
                    if not password:
                        self.log("2FA password not entered.")
                        return
                    await self.client.sign_in(password=password)

            self.me = await self.client.get_me()
            self.log("✅ Logged in successfully.")
            dialogs = await self.client.get_dialogs()
            chat_names = sorted([d.name for d in dialogs if d.name])
            self.dialogs = dialogs
            self.chat_listbox.delete(0, tk.END)
            for name in chat_names:
                self.chat_listbox.insert(tk.END, name)
            self.start_button.config(state="normal")

        except Exception as e:
            self.log(f"Login failed: {e}")

    def get_selected_chats(self):
        return [self.chat_listbox.get(i) for i in self.chat_listbox.curselection()]

    def load_last_ids(self):
        if os.path.exists(LAST_IDS_FILE):
            with open(LAST_IDS_FILE, "r") as f:
                return json.load(f)
        return {}

    def save_last_ids(self, data):
        with open(LAST_IDS_FILE, "w") as f:
            json.dump(data, f)

    def backup_job(self):
        asyncio.run_coroutine_threadsafe(self.backup_chats(), self.loop)

    async def backup_chats(self):
        selected_chats = self.get_selected_chats()
        if not selected_chats:
            self.log("⚠️ No chats selected for backup.")
            return

        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        last_ids = self.load_last_ids()
        total_texts = 0
        total_media = 0

        for chat_name in selected_chats:
            self.log(f"🔄 Backing up chat: {chat_name}")
            target = next((d.entity for d in self.dialogs if d.name == chat_name), None)
            if not target:
                self.log(f"❌ Chat not found: {chat_name}")
                continue

            folder = os.path.join(f"backup_{date_str}", chat_name.replace(" ", "_"))
            os.makedirs(os.path.join(folder, "media"), exist_ok=True)
            text_file = os.path.join(folder, "messages.html")

            # Create file with header + CSS if new
            if not os.path.exists(text_file):
                with open(text_file, "w", encoding="utf-8") as f:
                    f.write(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Telegram Backup - {chat_name}</title>
<style>
  body {{
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background: #f5f8fa;
    padding: 20px;
  }}
  .chat-container {{
    max-width: 600px;
    margin: auto;
    background: white;
    border-radius: 8px;
    padding: 15px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }}
  .message {{
    margin: 10px 0;
    max-width: 70%;
    padding: 10px 15px;
    border-radius: 18px;
    clear: both;
    position: relative;
    font-size: 14px;
    line-height: 1.3;
  }}
  .from-me {{
    background-color: #dcf8c6;
    float: right;
    text-align: right;
  }}
  .from-others {{
    background-color: #fff;
    border: 1px solid #e2e2e2;
    float: left;
    text-align: left;
  }}
  .sender {{
    font-weight: bold;
    font-size: 13px;
    margin-bottom: 3px;
  }}
  .timestamp {{
    font-size: 11px;
    color: #888;
    margin-top: 5px;
  }}
  img.media, video.media {{
    max-width: 100%;
    border-radius: 10px;
    margin-top: 5px;
  }}
  .document-preview {{
    display: flex;
    align-items: center;
    margin-top: 5px;
  }}
  .doc-icon {{
    width: 24px;
    height: 24px;
    margin-right: 8px;
    opacity: 0.7;
  }}
  a {{
    color: #065fd4;
    text-decoration: none;
  }}
  a:hover {{
    text-decoration: underline;
  }}
</style>
</head>
<body>
<div class="chat-container">
""")

            last_msg_id = last_ids.get(chat_name, 0)
            messages = await self.client.get_messages(target, min_id=last_msg_id, limit=200)
            messages = list(reversed(messages))

            if not messages:
                self.log(f"✅ No new messages for {chat_name}.")
                continue

            with open(text_file, "a", encoding="utf-8") as f:
                for msg in messages:
                    sender_name = "You" if msg.sender_id == (self.me.id if self.me else None) else (str(msg.sender_id) if msg.sender_id else "Unknown")
                    timestamp = msg.date.strftime("%Y-%m-%d %H:%M")
                    text = msg.message or ""
                    from_me_class = "from-me" if msg.sender_id == (self.me.id if self.me else None) else "from-others"
                    media_html = ""

                    if msg.media:
                        media_path = await msg.download_media(file=os.path.join(folder, "media"))
                        filename = os.path.basename(media_path)
                        ext = os.path.splitext(filename)[1].lower()

                        if ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp"]:
                            media_html = f'<img class="media" src="media/{filename}" alt="Image"/>'
                        elif ext in [".mp4", ".mov", ".avi"]:
                            media_html = f'<video class="media" controls><source src="media/{filename}" type="video/mp4">Your browser does not support the video tag.</video>'
                        elif ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx"]:
                            doc_icon_svg = '''
                                <svg class="doc-icon" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" 
                                     xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M7 7v10a2 2 0 002 2h6a2 2 0 002-2V7H7z"/>
                                  <path stroke-linecap="round" stroke-linejoin="round" d="M7 7l5 5 5-5"/>
                                </svg>
                            '''
                            media_html = f'<div class="document-preview">{doc_icon_svg}<a href="media/{filename}" target="_blank" download>{filename}</a></div>'
                        else:
                            media_html = f'<a href="media/{filename}" target="_blank" download>Download {filename}</a>'

                    # Escape text for HTML safety
                    import html
                    safe_text = html.escape(text).replace("\n", "<br>")

                    f.write(f"""
                    <div class="message {from_me_class}">
                      <div class="sender">{sender_name}</div>
                      <div class="text">{safe_text}</div>
                      {media_html}
                      <div class="timestamp">{timestamp}</div>
                    </div>
                    """)

            last_ids[chat_name] = messages[-1].id
            total_texts += len(messages)
            total_media += sum(1 for m in messages if m.media)
            self.log(f"✅ {len(messages)} messages backed up from '{chat_name}'.")

        self.save_last_ids(last_ids)

        # Close all open HTML containers for all chats (optional but neat)
        for chat_name in selected_chats:
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            folder = os.path.join(f"backup_{date_str}", chat_name.replace(" ", "_"))
            text_file = os.path.join(folder, "messages.html")
            # Append closing tags only if file exists
            if os.path.exists(text_file):
                with open(text_file, "a", encoding="utf-8") as f:
                    f.write("</div></body></html>")

        self.log(f"📦 Backup complete: {total_texts} messages, {total_media} media files saved.\n")

    def start_scheduler(self):
        if self.running:
            self.log("Scheduler already running.")
            return
        self.running = True
        schedule.clear()
        schedule.every().day.at("16:42").do(self.backup_job)
        self.log("⏰ Daily backup scheduled for 16:42.")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.scheduler_thread = threading.Thread(target=self.run_schedule, daemon=True)
        self.scheduler_thread.start()

    def stop_scheduler(self):
        self.running = False
        schedule.clear()
        self.log("🛑 Scheduler stopped.")
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

    def run_schedule(self):
        while self.running:
            schedule.run_pending()
            time.sleep(1)

if __name__ == "__main__":
    root = tk.Tk()
    app = TelegramBackupApp(root)
    root.mainloop()
