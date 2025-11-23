from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Transaksi, Notifikasi, Produk, Pelanggan
from .tasks import send_notification_email, send_feedback_reminder, send_product_restock_broadcast, ADMIN_EMAIL_LIST # Import task Celery kita

# Gunakan decorator @receiver untuk mendengarkan sinyal
@receiver(post_save, sender=Transaksi)
def handle_transaction_update(sender, instance, created, **kwargs):
    """
    Handler yang dipanggil setelah objek Transaksi disimpan (dibuat atau diupdate).
    
    Args:
        instance (Transaksi): Objek Transaksi yang baru disimpan.
        created (bool): True jika objek baru dibuat, False jika diupdate.
    """
    
    # Dapatkan ID Pelanggan dan Email
    pelanggan = instance.idPelanggan
    recipient_email = pelanggan.email
    pelanggan_name = pelanggan.nama_pelanggan
    
    # ----------------------------------------------------
    # Skenario 1: Transaksi Baru Dibuat (Status Awal: DIPROSES)
    # ----------------------------------------------------
    if created:
        subject = f"🥳 Transaksi Berhasil Dibuat (#ID{instance.id})"
        message = (
            f"Hai {pelanggan_name},\n\n"
            f"Terima kasih telah berbelanja di UD. Barokah Jaya Beton.\n"
            f"Nomor pesanan Anda adalah #{instance.id} dengan total Rp{instance.total}.\n"
            f"Status saat ini: {instance.status_transaksi}.\n\n"
            f"Segera lakukan pembayaran sebelum batas waktu berakhir!"
        )
        notification_type = "TRANSACTION_CREATED"

    # ----------------------------------------------------
    # Skenario 2: Status Diubah menjadi SELESAI
    # ----------------------------------------------------
    elif instance.status_transaksi == 'SELESAI':
        subject = f"✅ Pesanan Anda Selesai dan Diterima! (#ID{instance.id})"
        message = (
            f"Hai {pelanggan_name},\n\n"
            f"Pesanan Anda #{instance.id} telah berhasil diselesaikan.\n"
            f"Kami harap Anda puas dengan produk kami. Jangan lupa berikan feedback Anda!\n\n"
            f"Total Pembelian: Rp{instance.total}"
        )
        notification_type = "TRANSACTION_COMPLETED"

    # Jika tidak ada skenario yang cocok, keluar dari handler
    else:
        return

    # ----------------------------------------------------
    # Simpan ke Model Notifikasi (Internal Django)
    # ----------------------------------------------------
    Notifikasi.objects.create(
        idPelanggan=pelanggan,
        tipe_pesan=notification_type,
        isi_pesan=message,
        is_read=False
    )
    
    # ----------------------------------------------------
    # Memicu Task Celery (Pengiriman Email Asinkron)
    # ----------------------------------------------------
    if recipient_email:
        # Panggil task Celery menggunakan .delay()
        send_notification_email.delay(subject, message, [recipient_email], link_url=f"/transaksi/{instance.id}")
        print(f"Signal: Task Celery untuk Transaksi #{instance.id} dipicu.")
    else:
        print(f"Signal: Pelanggan #{pelanggan.id} tidak memiliki email, hanya simpan Notifikasi internal.")

    # ------------------------------------------------------------------
    # Tambahan: jika status berubah menjadi SELESAI, jadwalkan pengingat feedback
    # Gunakan _previous_status yang di-set pada pre_save (jika tersedia)
    prev_status = getattr(instance, '_previous_status', None)
    if not created and prev_status is not None and prev_status != instance.status_transaksi and instance.status_transaksi == 'SELESAI':
        reminder_subject = f"📝 Pengingat: Mohon Berikan Feedback untuk Pesanan #{instance.id}"
        reminder_message = (
            f"Hai {pelanggan.nama_pelanggan},\n\nTerima kasih telah berbelanja. Mohon luangkan waktu untuk memberikan feedback untuk pesanan Anda #{instance.id}."
        )
        # schedule after 3 days = 259200 seconds
        send_feedback_reminder.apply_async(countdown=259200, args=[instance.id, reminder_subject, reminder_message, [recipient_email], f"/feedback/{instance.id}"])
        print(f"Signal: Pengingat feedback dijadwalkan untuk Transaksi #{instance.id} (3 hari).")

    # ------------------------------------------------------------------
    # Notifikasi Admin: jika transaksi baru dibuat atau status berubah menjadi DIPROSES
    if created or (prev_status is not None and prev_status != instance.status_transaksi and instance.status_transaksi == 'DIPROSES'):
        admin_subject = f"📥 Transaksi Baru / Perlu Proses: #{instance.id}"
        admin_message = (
            f"Transaksi #{instance.id} oleh {pelanggan.nama_pelanggan} memerlukan perhatian."
        )
        send_notification_email.delay(admin_subject, admin_message, ADMIN_EMAIL_LIST, link_url=f"/admin/core/transaksi/{instance.id}/change/")
        print(f"Signal: Notifikasi admin dikirim untuk Transaksi #{instance.id}.")


@receiver(pre_save, sender=Transaksi)
def capture_previous_transaction_state(sender, instance, **kwargs):
    """Simpan status dan bukti_bayar sebelumnya pada instance sebelum disimpan."""
    if not instance.pk:
        instance._previous_bukti_bayar = None
        instance._previous_status = None
        return

    try:
        prev = Transaksi.objects.get(pk=instance.pk)
        instance._previous_bukti_bayar = prev.bukti_bayar
        instance._previous_status = prev.status_transaksi
    except Transaksi.DoesNotExist:
        instance._previous_bukti_bayar = None
        instance._previous_status = None


@receiver(post_save, sender=Transaksi)
def handle_bukti_upload_and_admin_notification(sender, instance, created, **kwargs):
    """Receiver tambahan untuk mendeteksi unggahan bukti_bayar dan mengirim notifikasi ke admin dan pelanggan."""
    prev_bukti = getattr(instance, '_previous_bukti_bayar', None)
    # Jika bukti_bayar baru diupload
    if instance.bukti_bayar and not prev_bukti:
        # Notifikasi konfirmasi ke pelanggan
        if instance.idPelanggan and instance.idPelanggan.email:
            subject_cust = f"📩 Bukti Pembayaran Diterima untuk Pesanan #{instance.id}"
            message_cust = (
                f"Hai {instance.idPelanggan.nama_pelanggan},\n\nTerima kasih. Bukti pembayaran untuk pesanan #{instance.id} telah kami terima dan akan diverifikasi oleh tim."
            )
            send_notification_email.delay(subject_cust, message_cust, [instance.idPelanggan.email], link_url=f"/transaksi/{instance.id}")

        # Notifikasi ke admin agar segera verifikasi
        admin_subject = f"🔔 Bukti Pembayaran Siap Diverifikasi: Pesanan #{instance.id}"
        admin_message = f"Bukti pembayaran untuk transaksi #{instance.id} telah diupload dan siap diverifikasi."
        send_notification_email.delay(admin_subject, admin_message, ADMIN_EMAIL_LIST, link_url=f"/admin/core/transaksi/{instance.id}/change/")
        print(f"Signal: Notifikasi bukti bayar dikirim untuk Transaksi #{instance.id}.")


@receiver(pre_save, sender=Produk)
def capture_previous_product_stock(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_stok = None
        return
    try:
        prev = Produk.objects.get(pk=instance.pk)
        instance._previous_stok = prev.stok_produk
    except Produk.DoesNotExist:
        instance._previous_stok = None


@receiver(post_save, sender=Produk)
def handle_product_restock(sender, instance, created, **kwargs):
    prev_stok = getattr(instance, '_previous_stok', None)
    # Jika terjadi restock signifikan: dari <5 ke >10
    if prev_stok is not None and prev_stok < 5 and instance.stok_produk > 10:
        send_product_restock_broadcast.delay(instance.id, link_url=f"/produk/{instance.id}")
        print(f"Signal: Broadcast restock dijadwalkan untuk Produk #{instance.id}.")