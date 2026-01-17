import customtkinter
import threading
import time
from pynput.mouse import Button, Controller
from pynput import keyboard

# Configuration
customtkinter.set_appearance_mode("Dark")
customtkinter.set_default_color_theme("blue")

class AutoclickerApp(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        self.title("Modern Autoclicker")
        self.geometry("400x450")
        self.resizable(False, False)

        # Variables
        self.running = False
        self.mouse = Controller()
        self.delay = 1.0
        self.button_to_click = Button.left
        self.stop_event = threading.Event()
        self.click_thread = None

        # --- GUI Layout ---
        
        # Title
        self.label_title = customtkinter.CTkLabel(self, text="Autoclicker", font=("Roboto", 24, "bold"))
        self.label_title.pack(pady=20)

        # Interval Frame
        self.frame_interval = customtkinter.CTkFrame(self)
        self.frame_interval.pack(pady=10, padx=20, fill="x")

        self.label_interval = customtkinter.CTkLabel(self.frame_interval, text="Click Interval (seconds):")
        self.label_interval.pack(pady=5)

        self.entry_interval = customtkinter.CTkEntry(self.frame_interval, placeholder_text="1.0")
        self.entry_interval.insert(0, "1.0")
        self.entry_interval.pack(pady=5)

        # Settings Frame
        self.frame_settings = customtkinter.CTkFrame(self)
        self.frame_settings.pack(pady=10, padx=20, fill="x")

        self.label_mb = customtkinter.CTkLabel(self.frame_settings, text="Mouse Button:")
        self.label_mb.pack(pady=5)

        self.option_mb = customtkinter.CTkOptionMenu(self.frame_settings, values=["Left", "Right", "Middle"], command=self.change_button)
        self.option_mb.pack(pady=5)

        # Status
        self.label_status = customtkinter.CTkLabel(self, text="Status: STOPPED", text_color="red", font=("Roboto", 16))
        self.label_status.pack(pady=20)

        # Controls
        self.btn_start = customtkinter.CTkButton(self, text="Start (F6)", command=self.start_clicking, fg_color="green", hover_color="darkgreen")
        self.btn_start.pack(pady=10)

        self.btn_stop = customtkinter.CTkButton(self, text="Stop (F6)", command=self.stop_clicking, fg_color="red", hover_color="darkred", state="disabled")
        self.btn_stop.pack(pady=10)

        # Hotkey Listener
        self.listener = keyboard.GlobalHotKeys({
            '<f6>': self.toggle_clicking
        })
        self.listener.start()

    def change_button(self, choice):
        if choice == "Left":
            self.button_to_click = Button.left
        elif choice == "Right":
            self.button_to_click = Button.right
        elif choice == "Middle":
            self.button_to_click = Button.middle

    def toggle_clicking(self):
        if self.running:
            self.stop_clicking()
        else:
            self.start_clicking()

    def start_clicking(self):
        if self.running:
            return
            
        try:
            self.delay = float(self.entry_interval.get())
        except ValueError:
            self.delay = 1.0 # Default fallback
            self.entry_interval.delete(0, "end")
            self.entry_interval.insert(0, "1.0")

        self.running = True
        self.stop_event.clear()
        self.label_status.configure(text="Status: RUNNING", text_color="green")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        
        self.click_thread = threading.Thread(target=self.run_clicker)
        self.click_thread.daemon = True
        self.click_thread.start()

    def stop_clicking(self):
        if not self.running:
            return
            
        self.running = False
        self.stop_event.set()
        self.label_status.configure(text="Status: STOPPED", text_color="red")
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def run_clicker(self):
        next_click = time.time()
        while not self.stop_event.is_set():
            if time.time() >= next_click:
                self.mouse.click(self.button_to_click)
                next_click = time.time() + self.delay
            else:
                time.sleep(0.01) # Small sleep to prevent CPU hogging

    def on_close(self):
        self.listener.stop()
        self.stop_clicking()
        self.destroy()

if __name__ == "__main__":
    app = AutoclickerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
