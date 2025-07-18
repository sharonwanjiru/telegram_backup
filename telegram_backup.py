# import the libraries
import asyncio
import threading
import schedule
import time
import os
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

class TelegramBackupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Backup Scheduler")

        tk.Label(root, text="API ID:").grid(row=0, column=0, sticky="e")
        tk.Label(root, text="API Hash:").grid(row=1, column=0, sticky="e")
        tk.Label(root, text="Phone Number:").grid(row=2, column=0, sticky="e")
        tk.Label(root, text="Select Chat:").grid(row=4, column=0, sticky="e")
        tk.Label(root, text="Backup Interval (minutes):").grid(row=5, column=0, sticky="e")

        self.api_id_entry = tk.Entry(root)
        self.api_hash_entry = tk.Entry(root)
        self.phone_entry = tk.Entry(root)
        self.interval_entry = tk.Entry(root)
        self.interval_entry.insert(0, "60")

        self.api_id_entry.grid(row=0, column=1)
        self.api_hash_entry.grid(row=1, column=1)
        self.phone_entry.grid(row=2, column=1)
        self.interval_entry.grid(row=5, column=1)

        self.chat_combo = ttk.Combobox(root, state="readonly")
        self.chat_combo.grid(row=4, column=1)

        self.status_text = tk.Text(root, height=10, width=50)
        self.status_text.grid(row=6, column=0, columnspan=2, pady=10)

        self.login_button = tk.Button(root, text="Login & Load Chats", command=self.login)
        self.start_button = tk.Button(root, text="Start Backup Scheduler", command=self.start_scheduler, state="disabled")
        self.stop_button = tk.Button(root, text="Stop Scheduler", command=self.stop_scheduler, state="disabled")

        self.login_button.grid(row=3, column=0, pady=5)
        self.start_button.grid(row=5, column=2, padx=5)
        self.stop_button.grid(row=6, column=2, padx=5)

        self.client = None
        self.scheduler_thread = None
        self.running = False
        self.loop = asyncio.new_event_loop()

        # Start the event loop in a background thread
        self.loop_thread = threading.Thread(target=self.run_loop, daemon=True)
        self.loop_thread.start()

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def log(self, message):
        self.status_text.insert(tk.END, message + "\n")
        self.status_text.see(tk.END)

    def prompt_user_input(self, prompt, title="Input", show=None):
        import queue
        result_queue = queue.Queue()

        def ask():
            result = simpledialog.askstring(title, prompt, show=show)
            result_queue.put(result)

        self.root.after(0, ask)
        return result_queue.get()  # Waits until user responds

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

        self.client = TelegramClient('pycharm_session', api_id, api_hash, loop=self.loop)

        # Schedule login coroutine on the event loop
        asyncio.run_coroutine_threadsafe(self._login(phone), self.loop)

    async def _login(self, phone):
        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.log("Sending code request...")
                await self.client.send_code_request(phone)
                self.log("Code sent to Telegram. Please enter it.")

                code = await self.loop.run_in_executor(None, lambda: self.prompt_user_input("Enter the login code you received:", "Telegram Login"))
                if not code:
                    self.log("Login cancelled: no code entered.")
                    return

                try:
                    await self.client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    self.log("Two-factor authentication enabled. Please enter your password.")
                    password = await self.loop.run_in_executor(None, lambda: self.prompt_user_input("Enter your 2FA password:", "2FA Password", show="*"))
                    if not password:
                        self.log("Login cancelled: no 2FA password entered.")
                        return
                    await self.client.sign_in(password=password)

            self.log("✅ Logged in successfully!")
            dialogs = await self.client.get_dialogs()
            chat_names = [d.name for d in dialogs if d.name]
            self.chat_combo['values'] = chat_names
            if chat_names:
                self.chat_combo.current(0)
            self.start_button.config(state="normal")
        except Exception as e:
            self.log(f"Login failed: {e}")

    def backup_job(self):
        asyncio.run_coroutine_threadsafe(self.backup_chat(), self.loop)

    async def backup_chat(self):
        chat_name = self.chat_combo.get()
        if not chat_name:
            self.log("No chat selected!")
            return
        self.log(f"Starting backup for '{chat_name}'...")
        dialogs = await self.client.get_dialogs()
        target = None
        for dialog in dialogs:
            if dialog.name == chat_name:
                target = dialog.entity
                break
        if not target:
            self.log(f"Chat '{chat_name}' not found!")
            return

        messages = await self.client.get_messages(target, limit=100)
        filename = f"telegram_backup_{chat_name.replace(' ', '_')}.txt"
        media_folder = f"telegram_media_{chat_name.replace(' ', '_')}"
        os.makedirs(media_folder, exist_ok=True)

        with open(filename, "w", encoding="utf-8") as f:
            for msg in messages:
                if msg.message:
                    f.write(f"{msg.date} - {msg.sender_id}: {msg.message}\n")
                if msg.media:
                    path = await msg.download_media(file=media_folder)
                    f.write(f"Media saved: {path}\n")

        self.log(f"✅ Backup completed for '{chat_name}'.")

    def start_scheduler(self):
        if self.running:
            self.log("Scheduler is already running.")
            return
        try:
            interval = int(self.interval_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Interval must be an integer.")
            return
        self.running = True
        schedule.clear()
        schedule.every(interval).minutes.do(self.backup_job)
        self.log(f"📅 Scheduler started. Running every {interval} minutes.")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.scheduler_thread = threading.Thread(target=self.run_schedule, daemon=True)
        self.scheduler_thread.start()

    def stop_scheduler(self):
        if not self.running:
            self.log("Scheduler is not running.")
            return
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
