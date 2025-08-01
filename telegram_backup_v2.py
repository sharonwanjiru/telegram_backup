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
import sys
import traceback

# Error logging to file
def global_exception_handler(exc_type, exc_value, exc_traceback):
    with open("crash.log", "w") as f:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)

sys.excepthook = global_exception_handler

LAST_IDS_FILE = "last_ids.json"

class TelegramBackupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Backup App")

        # --- UI Layout ---
        tk.Label(root, text="API ID:").grid(row=0, column=0, sticky="e")
        tk.Label(root, text="API Hash:").grid(row=1, column=0, sticky="e")
        tk.Label(root, text="Phone Number:").grid(row=2, column=0, sticky="e")
        tk.Label(root, text="Select Chat:").grid(row=3, column=0, sticky="e")

        self.api_id_entry = tk.Entry(root)
        self.api_hash_entry = tk.Entry(root)
        self.phone_entry = tk.Entry(root)

        self.api_id_entry.grid(row=0, column=1)
        self.api_hash_entry.grid(row=1, column=1)
        self.phone_entry.grid(row=2, column=1)

        self.chat_combo = ttk.Combobox(root, state="readonly")
        self.chat_combo.grid(row=3, column=1)

        self.status_text = tk.Text(root, height=10, width=60)
        self.status_text.grid(row=5, column=0, columnspan=3, pady=10)

        self.progress = ttk.Progressbar(root, orient="horizontal", length=400, mode="determinate")
        self.progress.grid(row=6, column=0, columnspan=3, pady=5)

        self.login_button = tk.Button(root, text="Login & Load Chats", command=self.login)
        self.start_button = tk.Button(root, text="Start Daily Backup", command=self.start_scheduler, state="disabled")
        self.stop_button = tk.Button(root, text="Stop Scheduler", command=self.stop_scheduler, state="disabled")

        self.login_button.grid(row=4, column=0, pady=5)
        self.start_button.grid(row=4, column=1)
        self.stop_button.grid(row=4, column=2)

        # --- Async setup ---
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

            self.log("✅ Logged in successfully.")
            dialogs = await self.client.get_dialogs()
            chat_names = sorted([d.name for d in dialogs if d.name])
            self.chat_combo['values'] = chat_names
            if chat_names:
                self.chat_combo.current(0)
            self.start_button.config(state="normal")

        except Exception as e:
            self.log(f"Login failed: {e}")

    def load_last_ids(self):
        if os.path.exists(LAST_IDS_FILE):
            with open(LAST_IDS_FILE, "r") as f:
                return json.load(f)
        return {}

    def save_last_ids(self, data):
        with open(LAST_IDS_FILE, "w") as f:
            json.dump(data, f)

    def backup_job(self):
        asyncio.run_coroutine_threadsafe(self.backup_chat(), self.loop)

    async def backup_chat(self):
        chat_name = self.chat_combo.get()
        if not chat_name:
            self.log("No chat selected.")
            return

        self.log(f"Backing up chat: {chat_name}")
        dialogs = await self.client.get_dialogs()
        target = next((d.entity for d in dialogs if d.name == chat_name), None)
        if not target:
            self.log(f"Chat '{chat_name}' not found.")
            return

        folder_name = datetime.datetime.now().strftime("backup_%Y-%m-%d")
        base_folder = os.path.join(os.getcwd(), folder_name, chat_name.replace(" ", "_"))
        media_folder = os.path.join(base_folder, "media")
        os.makedirs(media_folder, exist_ok=True)
        text_file = os.path.join(base_folder, "messages.txt")

        last_ids = self.load_last_ids()
        last_msg_id = last_ids.get(chat_name, 0)

        new_messages = await self.client.get_messages(target, min_id=last_msg_id, limit=200)
        new_messages = list(reversed(new_messages))

        if not new_messages:
            self.log("No new messages.")
            return

        self.progress['maximum'] = len(new_messages)
        self.progress['value'] = 0

        media_download_tasks = []

        with open(text_file, "a", encoding="utf-8") as f:
            for i, msg in enumerate(new_messages, 1):
                if msg.message:
                    f.write(f"[{msg.date.strftime('%Y-%m-%d %H:%M')}] {msg.sender_id}: {msg.message}\n")
                if msg.media:
                    # Schedule media downloads concurrently
                    coro = msg.download_media(file=media_folder)
                    media_download_tasks.append(coro)
                    # We'll log media path after download is done
                f.write("\n")
                # Update progress bar on UI thread
                self.progress['value'] = i
                self.status_text.update_idletasks()

        if media_download_tasks:
            self.log(f"Downloading {len(media_download_tasks)} media files concurrently...")
            media_paths = await asyncio.gather(*media_download_tasks)
            with open(text_file, "a", encoding="utf-8") as f:
                for path in media_paths:
                    if path:
                        f.write(f"[Media saved]: {path}\n")

        last_ids[chat_name] = new_messages[-1].id
        self.save_last_ids(last_ids)

        self.log(f"✅ {len(new_messages)} messages backed up from '{chat_name}'.")
        self.progress['value'] = 0

    def start_scheduler(self):
        if self.running:
            self.log("Scheduler is already running.")
            return
        self.running = True
        schedule.clear()
        schedule.every().day.at("12:35").do(self.backup_job)
        self.log("📅 Daily backup scheduled for 12:20.")
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

    # Wait until async loop is running to avoid premature coroutine calls
    while not app.loop.is_running():
        time.sleep(0.1)

    root.mainloop()
