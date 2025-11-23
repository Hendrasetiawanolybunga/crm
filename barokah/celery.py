
import os
from celery import Celery

# 1. Set default settings module untuk program 'celery'.
#    Ini memberitahu Celery di mana menemukan settings.py Anda.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'barokah.settings')

# 2. Buat instance Celery.
app = Celery('barokah')

# 3. Muat konfigurasi Celery dari file settings.py Django.
#    Namespace 'CELERY' artinya semua konfigurasi harus diawali CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# 4. Temukan tugas (tasks) dari semua file tasks.py di aplikasi yang terdaftar.
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    """
    Fungsi debug sederhana untuk memverifikasi Celery berjalan.
    """
    print(f'Request: {self.request!r}')