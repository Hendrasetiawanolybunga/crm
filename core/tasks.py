from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import date, timedelta
from django.db.models import Sum
# Import model yang dibutuhkan
from .models import Pelanggan, Transaksi, Produk

# Placeholder admin email list sesuai permintaan
ADMIN_EMAIL_LIST = ['admin@barokah.com']

# --- TASK EMAIL DASAR ---
@shared_task(bind=True) # Menggunakan bind=True agar bisa mengakses self.retry
def send_notification_email(self, subject, message, recipient_list, link_url=None, link_text="Lihat Detail"):
    """
    Tugas Celery untuk mengirim email notifikasi.
    
    Argumen:
    - subject (str): Subjek email.
    - message (str): Isi pesan email.
    - recipient_list (list): Daftar alamat email penerima.
    """
    
    # Ambil email pengirim dari settings.py
    from_email = settings.DEFAULT_FROM_EMAIL
    
    try:
        # Jika link disediakan, tambahkan format hyperlink sederhana ke message
        if link_url:
            message_with_link = f"{message}\n\n[Link: {link_text}]({link_url})"
        else:
            message_with_link = message

        # Panggil fungsi send_mail bawaan Django
        send_mail(
            subject,
            message_with_link,
            from_email,
            recipient_list,
            fail_silently=False,
        )
        # Log di worker terminal jika pengiriman berhasil
        print(f"✅ Email berhasil dikirim ke {recipient_list} dengan subjek: {subject}")
        return "Email sent successfully"
        
    except Exception as e:
        # Log error jika pengiriman gagal
        print(f"❌ Gagal mengirim email ke {recipient_list}. Error: {e}")
        # Celery akan mencoba ulang (retry) jika terjadi error
        raise self.retry(exc=e, countdown=60) # Coba lagi 60 detik kemudian

# --- TASK TERJADWAL (CELERY BEAT) ---

@shared_task
def check_payment_deadlines():
    """
    Memeriksa transaksi yang telah melewati batas waktu pembayaran
    dan membatalkannya secara otomatis.
    """
    now = timezone.now()
    
    # Mencari transaksi yang statusnya 'DIPROSES' ATAU 'MENUNGGU VERIFIKASI' 
    # dan batas waktu bayarnya sudah terlewat
    expired_transactions = Transaksi.objects.filter(
        status_transaksi__in=['DIPROSES', 'MENUNGGU VERIFIKASI'],
        batas_waktu_bayar__lte=now
    ).exclude(idPelanggan__email__isnull=True).exclude(idPelanggan__email__exact='')

    count = expired_transactions.count()
    if count > 0:
        print(f"🚨 Ditemukan {count} transaksi yang melewati batas waktu pembayaran. Membatalkan...")
        
        for transaksi in expired_transactions:
            # Mengubah status menjadi DIBATALKAN
            transaksi.status_transaksi = 'DIBATALKAN'
            transaksi.save() # Panggilan save() ini akan memicu notifikasi perubahan status
            
            # Mengirim notifikasi pembatalan
            subject = f"❌ Pesanan Dibatalkan Otomatis #{transaksi.id} (Barokah Beton)"
            message = (
                f"Hai {transaksi.idPelanggan.nama_pelanggan},\n\n"
                f"Pesanan Anda dengan nomor **#{transaksi.id}** telah dibatalkan secara otomatis "
                f"karena melewati batas waktu pembayaran ({transaksi.batas_waktu_bayar.strftime('%d %b %Y %H:%M:%S')}).\n\n"
                f"Anda dapat membuat pesanan baru melalui website kami."
            )
            
            # Memanggil tugas Celery untuk mengirim email
            send_notification_email.delay(subject, message, [transaksi.idPelanggan.email], link_url=f"/transaksi/{transaksi.id}")
            
        print("✅ Proses pembatalan otomatis selesai.")
    else:
        print("✅ Tidak ada transaksi yang perlu dibatalkan hari ini.")


@shared_task
def disable_birthday_discounts():
    """
    Memeriksa pelanggan yang diskon ulang tahunnya sudah aktif lebih dari 24 jam 
    dan menonaktifkan diskon tersebut.
    """
    # Batas waktu: 24 jam yang lalu
    twenty_four_hours_ago = timezone.now() - timedelta(hours=24)
    
    # Mencari pelanggan yang diskonnya aktif DAN diaktifkan lebih dari 24 jam yang lalu
    expired_discounts = Pelanggan.objects.filter(
        is_birthday_discount_active=True,
        birthday_discount_activated_at__lte=twenty_four_hours_ago # lte = less than or equal to
    )

    count = expired_discounts.update(is_birthday_discount_active=False, birthday_discount_activated_at=None)
    
    if count > 0:
        print(f"😴 Menonaktifkan {count} diskon ulang tahun yang sudah kedaluwarsa.")
    else:
        print("✅ Tidak ada diskon ulang tahun yang kedaluwarsa hari ini.")


@shared_task
def send_birthday_greetings():
    """
    Memeriksa pelanggan yang berulang tahun hari ini, menghitung loyalitas, 
    mengaktifkan diskon, dan mengirimkan ucapan selamat.
    """
    today = date.today()
    
    # Mencari pelanggan yang berulang tahun hari ini
    birthday_pelanggan_list = Pelanggan.objects.filter(
        tanggal_lahir__month=today.month,
        tanggal_lahir__day=today.day
    ).exclude(email__isnull=True).exclude(email__exact='')

    count = birthday_pelanggan_list.count()
    if count > 0:
        print(f"🥳 Ditemukan {count} pelanggan yang berulang tahun hari ini. Mengirim ucapan...")
        
        for pelanggan in birthday_pelanggan_list:
            
            # --- 1. HITUNG TOTAL RIWAYAT BELANJA (LOYALITAS) ---
            # Menghitung total jumlah pembayaran dari transaksi yang sudah SELESAI
            total_spent = Transaksi.objects.filter(
                idPelanggan=pelanggan,
                status_transaksi='SELESAI' # Hanya hitung yang sudah selesai
            ).aggregate(total=Sum('total'))['total'] or 0.00 # Menggunakan field 'total' di model Transaksi
            
            # Update field total_riwayat_belanja 
            pelanggan.total_riwayat_belanja = total_spent

            # --- 2. TENTUKAN DISKON DAN PESAN ---
            diskon_persentase = 0
            pesan_loyalitas = ""
            
            # Skenario 2: Pelanggan Loyal (Total Belanja >= Rp 5 Juta)
            if total_spent >= 5000000: 
                diskon_persentase = 10
                pesan_loyalitas = (
                    f"🎉 Selamat! Karena total riwayat belanja Anda mencapai Rp{total_spent:,.0f}, "
                    f"Anda mendapatkan **Diskon Loyalitas Tambahan sebesar 10%** untuk semua produk "
                    f"selama 24 jam ke depan! Diskon ini akan otomatis terhitung di keranjang Anda."
                )
            # Skenario 1: Ulang Tahun Biasa (Potongan 5 Juta)
            else: 
                pesan_loyalitas = (
                    "🎁 Selamat Ulang Tahun! Anda berhak mendapatkan **Potongan Harga Spesial** "
                    "jika total belanja Anda di keranjang saat ini mencapai Rp5 Juta. "
                    "Segera kunjungi website kami untuk mengklaimnya dalam 24 jam ini!"
                )
            
            # --- 3. AKTIFKAN DISKON DAN KIRIM EMAIL ---
            subject = f"🥳 Selamat Ulang Tahun & Klaim Diskon Anda, {pelanggan.nama_pelanggan}!"
            message = (
                f"Hai {pelanggan.nama_pelanggan},\n\n"
                f"Segenap tim UD. Barokah Jaya Beton mengucapkan selamat ulang tahun! Semoga panjang umur dan sukses selalu.\n\n"
                f"{pesan_loyalitas}\n\n"
                f"Terima kasih telah menjadi pelanggan setia kami."
            )

            # Aktifkan flag diskon
            pelanggan.is_birthday_discount_active = True
            pelanggan.birthday_discount_activated_at = timezone.now()
            # Simpan perubahan model (termasuk total_riwayat_belanja dan flag diskon)
            pelanggan.save() 
            
            # Memanggil tugas Celery untuk mengirim email
            send_notification_email.delay(subject, message, [pelanggan.email], link_url=f"/pelanggan/{pelanggan.id}")

        print("✅ Proses pengiriman ucapan ulang tahun dan aktivasi diskon selesai.")
    else:
        print("✅ Tidak ada pelanggan yang berulang tahun hari ini.")


@shared_task
def send_feedback_reminder(transaksi_pk, subject, message, recipient_list, link_url=None):
    """
    Tugas yang dijadwalkan untuk mengingatkan pelanggan memberikan feedback.
    Memeriksa apakah feedback masih kosong sebelum mengirim.
    """
    try:
        transaksi = Transaksi.objects.get(pk=transaksi_pk)
    except Transaksi.DoesNotExist:
        print(f"⚠️ Transaksi #{transaksi_pk} tidak ditemukan. Membatalkan pengingat feedback.")
        return

    # Cek apakah feedback tetap kosong
    if not transaksi.feedback:
        send_notification_email.delay(subject, message, recipient_list, link_url=link_url)
        print(f"✅ Pengingat feedback dikirim untuk Transaksi #{transaksi_pk} ke {recipient_list}")
    else:
        print(f"ℹ️ Transaksi #{transaksi_pk} sudah memiliki feedback, tidak mengirim pengingat.")


@shared_task
def check_and_send_payment_reminder():
    """
    Mencari transaksi yang akan jatuh tempo dalam 1 hingga 24 jam dan belum diberi reminder.
    """
    now = timezone.now()
    one_hour = now + timedelta(hours=1)
    twenty_four_hours = now + timedelta(hours=24)

    candidates = Transaksi.objects.filter(
        status_transaksi__in=['DIPROSES', 'MENUNGGU VERIFIKASI'],
        batas_waktu_bayar__gte=one_hour,
        batas_waktu_bayar__lte=twenty_four_hours,
        is_payment_reminder_sent=False
    ).exclude(idPelanggan__email__isnull=True).exclude(idPelanggan__email__exact='')

    for t in candidates:
        subject = f"⏰ Pengingat Pembayaran: Pesanan #{t.id}"
        message = (
            f"Hai {t.idPelanggan.nama_pelanggan},\n\n"
            f"Pesanan Anda dengan nomor #{t.id} akan jatuh tempo pada {t.batas_waktu_bayar.strftime('%d %b %Y %H:%M:%S')}.\n"
            f"Silakan lakukan pembayaran sebelum batas waktu untuk menghindari pembatalan.")

        send_notification_email.delay(subject, message, [t.idPelanggan.email], link_url=f"/pembayaran/{t.id}")
        t.is_payment_reminder_sent = True
        t.save(update_fields=['is_payment_reminder_sent'])
        print(f"✅ Pengingat pembayaran dikirim untuk Transaksi #{t.id}")


@shared_task
def send_product_restock_broadcast(product_pk, link_url=None):
    """
    Mengirim broadcast email ke semua pelanggan bahwa produk telah di-restock.
    """
    try:
        product = Produk.objects.get(pk=product_pk)
    except Produk.DoesNotExist:
        print(f"⚠️ Produk #{product_pk} tidak ditemukan. Broadcast dibatalkan.")
        return

    pelanggan_list = Pelanggan.objects.exclude(email__isnull=True).exclude(email__exact='')
    recipient_emails = [p.email for p in pelanggan_list]

    subject = f"🛍️ Produk Kembali Tersedia: {product.nama_produk}"
    message = (
        f"Hai,\n\nProduk '{product.nama_produk}' telah tersedia kembali di toko kami.\n"
        f"Segera kunjungi halaman produk untuk melakukan pembelian."
    )

    if recipient_emails:
        send_notification_email.delay(subject, message, recipient_emails, link_url=link_url or f"/produk/{product.id}")
        # Update trigger date
        product.last_restock_trigger_date = timezone.now()
        product.save(update_fields=['last_restock_trigger_date'])
        print(f"✅ Broadcast restock dikirim untuk Produk #{product_pk} ke {len(recipient_emails)} pelanggan")
    else:
        print("ℹ️ Tidak ada pelanggan dengan email, broadcast restock dilewatkan.")


@shared_task
def check_for_low_stock():
    """
    Mencari produk dengan stok di bawah threshold dan mengirim ringkasan ke admin.
    """
    LOW_STOCK_THRESHOLD = 5
    low_products = Produk.objects.filter(stok_produk__lt=LOW_STOCK_THRESHOLD)

    if not low_products.exists():
        print("✅ Tidak ada produk dengan stok rendah hari ini.")
        return

    lines = []
    for p in low_products:
        lines.append(f"{p.nama_produk} (ID:{p.id}) - Stok: {p.stok_produk} -> /admin/core/produk/{p.id}/change/")

    subject = "⚠️ Laporan Stok Rendah - Barokah"
    message = "Produk dengan stok rendah:\n\n" + "\n".join(lines)

    send_notification_email.delay(subject, message, ADMIN_EMAIL_LIST, link_url="/admin/core/produk/")
    print(f"✅ Laporan stok rendah dikirim ke admin ({len(ADMIN_EMAIL_LIST)} penerima).")