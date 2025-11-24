from django.db import models
from django.db.models import Sum, F
from django.utils import timezone 
from django.contrib.auth.hashers import make_password, check_password, identify_hasher
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from decimal import Decimal

# --- Model Pelanggan (Dengan Field Loyalitas/Diskon Ultah) ---
# ... (Kode Pelanggan tidak berubah, hanya import Decimal yang ditambahkan)
class Pelanggan(models.Model):
    id = models.AutoField(primary_key=True)
    nama_pelanggan = models.CharField(max_length=255, verbose_name="Nama Pelanggan")
    alamat = models.TextField(verbose_name="Alamat")
    tanggal_lahir = models.DateField(verbose_name="Tanggal Lahir")
    no_hp = models.CharField(max_length=20, verbose_name="Nomor HP")
    username = models.CharField(max_length=150, unique=True, verbose_name="Username")
    password = models.CharField(max_length=128, verbose_name="Password") 
    email = models.EmailField(max_length=254, unique=True, null=True, blank=True)

    # 🚨 TAMBAHAN UNTUK NOTIFIKASI ULTAH/LOYALITAS (Celery Task akan mengubah ini)
    is_birthday_discount_active = models.BooleanField(
        default=False, 
        verbose_name="Diskon Ultah Aktif"
    )
    birthday_discount_activated_at = models.DateTimeField(
        null=True, 
        blank=True, 
        verbose_name="Waktu Diskon Aktif"
    )
    # Field ini tidak digunakan di logika diskon, tetapi bagus untuk CRM display
    total_riwayat_belanja = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0.00,
        verbose_name="Total Riwayat Belanja"
    )

    class Meta:
        verbose_name_plural = "Pelanggan"
        db_table = 'pelanggan'

    def __str__(self):
        return str(self.nama_pelanggan)

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        """Return True if the given raw_password matches the stored password.
        
        This handles both hashed and legacy plain-text passwords: if the stored
        password is not a recognized hashed format, it will compare plain text
        and re-hash on success.
        """
        try:
            # If identify_hasher doesn't raise, assume it's a hashed password
            identify_hasher(self.password)
            return check_password(raw_password, self.password)
        except Exception:
            # Legacy plain-text password fallback (rehash on success)
            if raw_password == self.password:
                # re-hash and save for security
                self.password = make_password(raw_password)
                try:
                    self.save(update_fields=['password'])
                except Exception:
                    pass
                return True
            return False

    @classmethod
    def get_top_purchased_products(cls, pelanggan_id, limit=3):
        """
        Get the top purchased products for a customer
        """
        from django.db.models import Sum
        from .models import Transaksi, DetailTransaksi, Produk
        
        # Get successful transactions for this customer
        successful_transactions = Transaksi.objects.filter(
            idPelanggan_id=pelanggan_id,
            status_transaksi__in=['DIBAYAR', 'DIKIRIM', 'SELESAI']
        )
        
        # Get top products based on quantity purchased
        top_products = DetailTransaksi.objects.filter(
            idTransaksi__in=successful_transactions
        ).values(
            'idProduk'
        ).annotate(
            total_quantity=Sum('jumlah_produk')
        ).order_by('-total_quantity')[:limit]
        
        # Extract product IDs
        product_ids = [item['idProduk'] for item in top_products]
        
        # Return product objects
        return Produk.objects.filter(id__in=product_ids)

# --- Model Kategori ---
# ... (Kode Kategori tidak berubah)
class Kategori(models.Model):
    id = models.AutoField(primary_key=True)
    nama_kategori = models.CharField(max_length=255, verbose_name="Nama Kategori")

    class Meta:
        verbose_name_plural = "Kategori"
        db_table = 'kategori'

    def __str__(self):
        return str(self.nama_kategori)

# --- Model Produk ---
# ... (Kode Produk tidak berubah)
class Produk(models.Model):
    id = models.AutoField(primary_key=True)
    nama_produk = models.CharField(max_length=255, verbose_name="Nama Produk")
    deskripsi_produk = models.TextField(verbose_name="Deskripsi Produk")
    foto_produk = models.ImageField(upload_to='produk_images/', verbose_name="Foto Produk")
    stok_produk = models.IntegerField(verbose_name="Stok Produk")
    harga_produk = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Harga Produk")
    kategori = models.ForeignKey(Kategori, on_delete=models.SET_NULL, blank=True, null=True, verbose_name="Kategori")
    last_restock_trigger_date = models.DateTimeField(null=True, blank=True, verbose_name="Tanggal Trigger Restock Broadcast")

    class Meta:
        verbose_name_plural = "Produk"
        db_table = 'produk'

    def __str__(self):
        return str(self.nama_produk)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Simpan stok awal untuk deteksi perubahan restock
        try:
            self._original_stok = self.stok_produk
        except Exception:
            self._original_stok = None

# --- Pilihan (Choices) untuk model Transaksi ---
STATUS_TRANSAKSI_CHOICES = [
    ('DIPROSES', 'Diproses'),
    ('MENUNGGU VERIFIKASI', 'Menunggu Verifikasi'), # Status baru setelah upload bukti bayar
    ('DIBAYAR', 'Dibayar'),
    ('DIKIRIM', 'Dikirim'),
    ('SELESAI', 'Selesai'),
    ('DIBATALKAN', 'Dibatalkan'),
]

# --- Model Transaksi (Dengan Logika Notifikasi Perubahan Status) ---
class Transaksi(models.Model):
    id = models.AutoField(primary_key=True)
    tanggal = models.DateTimeField(default=timezone.now, verbose_name="Tanggal Transaksi") 
    total = models.DecimalField(max_digits=15, decimal_places=2, verbose_name="Total Keseluruhan", default=Decimal('0.00')) # Ditingkatkan max_digits dan default
    ongkir = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Ongkos Kirim", default=Decimal('0.00')) # Default Decimal
    status_transaksi = models.CharField(
        max_length=50, 
        choices=STATUS_TRANSAKSI_CHOICES, 
        default='DIPROSES', 
        verbose_name="Status Transaksi"
    )
    bukti_bayar = models.FileField(upload_to='bukti_pembayaran/', verbose_name="Bukti Pembayaran", null=True, blank=True)
    idPelanggan = models.ForeignKey(Pelanggan, on_delete=models.CASCADE, verbose_name="Pelanggan")
    alamat_pengiriman = models.TextField(verbose_name="Alamat Pengiriman", blank=True, null=True)
    feedback = models.TextField(verbose_name="Feedback", null=True, blank=True)
    fotofeedback = models.ImageField(upload_to='feedback_images/', verbose_name="Foto Feedback", null=True, blank=True)
    
    waktu_checkout = models.DateTimeField(default=timezone.now)
    batas_waktu_bayar = models.DateTimeField(null=True, blank=True)
    is_payment_reminder_sent = models.BooleanField(default=False, verbose_name="Pengingat Pra-Jatuh Tempo Terkirim")
    
    # 🚨 TAMBAHAN UNTUK NOTIFIKASI PERUBAHAN STATUS
    _original_status = None 

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Simpan status saat objek diinisialisasi/dimuat dari DB
        self._original_status = self.status_transaksi

    def save(self, *args, **kwargs):
        # Cek apakah status transaksi telah berubah
        status_changed = self._original_status != self.status_transaksi
        
        # Perbarui flag pengingat pembayaran
        if status_changed:
            if self.status_transaksi in ['DIBAYAR', 'DIBATALKAN', 'SELESAI']:
                self.is_payment_reminder_sent = True
            else:
                self.is_payment_reminder_sent = False

        # 1. Simpan objek terlebih dahulu
        super().save(*args, **kwargs)

        # 2. Jika status berubah, kirim notifikasi (LOGIC CELERY TIDAK DIGANGGU)
        if status_changed and self.idPelanggan.email:
            # Import tugas Celery di sini untuk mencegah Circular Import
            from .tasks import send_notification_email 
            
            subject = f"📣 Perubahan Status Pesanan #{self.id} (Barokah Beton)"
            message = (
                f"Hai {self.idPelanggan.nama_pelanggan},\n\n"
                f"Status pesanan Anda dengan nomor **#{self.id}** telah diperbarui oleh Admin.\n\n"
                f"Status Lama: {self._original_status}\n"
                f"Status Baru: **{self.status_transaksi}**\n\n"
                f"Silakan cek detail pesanan Anda di website."
            )
            
            # Kirim notifikasi menggunakan Celery (asynchronous)
            send_notification_email.delay(subject, message, [self.idPelanggan.email])

        # 3. Update status lama untuk panggilan save() berikutnya (penting)
        self._original_status = self.status_transaksi
    
    def calculate_total(self):
        """Menghitung dan memperbarui Transaksi.total (Sub Total Detail + Ongkir)"""
        # Hitung Sub Total dari semua Detail Transaksi
        sub_total_result = self.detailtransaksi_set.aggregate(sum_sub_total=Sum('sub_total'))
        sub_total = sub_total_result['sum_sub_total'] or Decimal('0.00')
        
        # Hitung Total Keseluruhan (Sub Total + Ongkir)
        new_total = sub_total + (self.ongkir or Decimal('0.00'))

        # Perbarui field total jika ada perubahan
        if self.total != new_total:
            self.total = new_total
            self.save(update_fields=['total'])


    class Meta:
        verbose_name_plural = "Transaksi"
        db_table = 'transaksi'

    def __str__(self):
        pelanggan_nama = getattr(self.idPelanggan, 'nama_pelanggan', 'Pelanggan')
        return f"Transaksi #{self.id} oleh {pelanggan_nama}"

# --- Model DetailTransaksi (Ditingkatkan dengan Signal) ---
class DetailTransaksi(models.Model):
    id = models.AutoField(primary_key=True)
    idTransaksi = models.ForeignKey(Transaksi, on_delete=models.CASCADE, verbose_name="Transaksi")
    idProduk = models.ForeignKey(Produk, on_delete=models.CASCADE, verbose_name="Produk")
    jumlah_produk = models.IntegerField(verbose_name="Jumlah Produk", default=1)
    # Default Decimal ditambahkan
    sub_total = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Sub Total", default=Decimal('0.00'), blank=True, null=True)

    class Meta:
        verbose_name_plural = "Detail Transaksi"
        db_table = 'detail_transaksi'

    def __str__(self):
        produk_nama = getattr(self.idProduk, 'nama_produk', 'Produk')
        return f"{self.jumlah_produk}x {produk_nama}"


# --- SIGNAL HANDLERS UNTUK PERHITUNGAN OTOMATIS ---

@receiver(pre_save, sender=DetailTransaksi)
def calculate_sub_total(sender, instance, **kwargs):
    """Hitung sub_total sebelum DetailTransaksi disimpan (pre_save)"""
    if instance.idProduk and instance.jumlah_produk is not None:
        # Pastikan idProduk sudah dimuat untuk mendapatkan harga
        try:
            # Menggunakan harga_produk dari Produk terkait
            harga = instance.idProduk.harga_produk
            instance.sub_total = harga * instance.jumlah_produk
        except Produk.DoesNotExist:
            # Handle case where related Produk is not found (shouldn't happen with FK)
            instance.sub_total = Decimal('0.00')
    else:
        instance.sub_total = Decimal('0.00')

@receiver(post_save, sender=DetailTransaksi)
def update_transaction_total_on_detail_change(sender, instance, **kwargs):
    """Perbarui total Transaksi setelah DetailTransaksi disimpan/diperbarui (post_save)"""
    # Pastikan idTransaksi ada (yaitu, DetailTransaksi bukan object yang sedang dibuat)
    if instance.idTransaksi:
        instance.idTransaksi.calculate_total()
        
@receiver(post_delete, sender=DetailTransaksi)
def update_transaction_total_on_detail_delete(sender, instance, **kwargs):
    """Perbarui total Transaksi setelah DetailTransaksi dihapus (post_delete)"""
    if instance.idTransaksi:
        # Transaksi mungkin sudah terhapus, jadi cek keberadaan
        try:
            instance.idTransaksi.calculate_total()
        except Transaksi.DoesNotExist:
            pass

@receiver(pre_save, sender=Transaksi)
def update_total_on_ongkir_change(sender, instance, **kwargs):
    """Hitung ulang total jika Transaksi.ongkir berubah (pre_save)"""
    # Hanya jalankan jika instance sudah memiliki primary key (bukan object baru)
    if instance.pk:
        # Load nilai lama dari database untuk perbandingan
        try:
            old_instance = Transaksi.objects.get(pk=instance.pk)
            # Cek jika ongkir atau status_transaksi berubah (status change trigger save, yang memanggil calculate_total)
            if old_instance.ongkir != instance.ongkir:
                # Karena ini pre_save, kita tidak bisa memanggil calculate_total() yang melakukan save().
                # calculate_total akan dipanggil di post_save dari DetailTransaksi, atau 
                # kita biarkan Transaksi.save() di admin.py/form save yang akan memicu ini.
                # Untuk kasus perubahan Ongkir saja, kita akan membiarkan Trannsaksi.calculate_total() 
                # dipanggil secara terpisah. Disini kita hanya perlu memastikan totalnya diperbarui.

                # Kita akan memanggil calculate_total pada post_save Transaksi. 
                # Namun untuk admin, karena kita menghapus save_formset, 
                # kita perlu memastikan calculate_total dipanggil saat save Transaksi di admin.
                pass 
        except Transaksi.DoesNotExist:
            pass

@receiver(post_save, sender=Transaksi)
def call_calculate_total_on_transaksi_save(sender, instance, **kwargs):
    """Pastikan total diperbarui saat Transaksi (termasuk ongkir) disimpan"""
    # Ini memastikan bahwa perubahan pada Ongkir atau status Transaksi 
    # akan memicu perhitungan total akhir (SubTotal + Ongkir)
    instance.calculate_total()


# --- Model DiskonPelanggan (Tidak Berubah) ---
STATUS_DISKON_CHOICES = [
    ('aktif', 'Aktif'),
    ('tidak_aktif', 'Tidak Aktif'),
]

class DiskonPelanggan(models.Model):
    id = models.AutoField(primary_key=True)
    idPelanggan = models.ForeignKey(Pelanggan, on_delete=models.CASCADE, verbose_name="Pelanggan")
    idProduk = models.ForeignKey(Produk, on_delete=models.CASCADE, verbose_name="Produk", null=True, blank=True)
    persen_diskon = models.IntegerField(verbose_name="Persen Diskon")
    status = models.CharField(
        max_length=50, 
        choices=STATUS_DISKON_CHOICES, 
        default='aktif', 
        verbose_name="Status"
    )
    pesan = models.TextField(verbose_name="Pesan", null=True, blank=True)
    tanggal_dibuat = models.DateTimeField(auto_now_add=True, verbose_name="Tanggal Dibuat")

    class Meta:
        verbose_name_plural = "Diskon Pelanggan"
        db_table = 'diskon_pelanggan'

    def __str__(self):
        pelanggan_nama = getattr(self.idPelanggan, 'nama_pelanggan', 'Pelanggan')
        return f"Diskon {self.persen_diskon}% untuk {pelanggan_nama}"

# --- Model Notifikasi (Tidak Berubah) ---
class Notifikasi(models.Model):
    id = models.AutoField(primary_key=True)
    idPelanggan = models.ForeignKey(Pelanggan, on_delete=models.CASCADE, verbose_name="Pelanggan")
    tipe_pesan = models.CharField(max_length=50, verbose_name="Tipe Pesan")
    isi_pesan = models.TextField(verbose_name="Isi Pesan")
    is_read = models.BooleanField(default=False, verbose_name="Sudah Dibaca")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Waktu Dibuat")

    class Meta:
        verbose_name_plural = "Notifikasi"
        db_table = 'notifikasi'
    
    def __str__(self):
        pelanggan_nama = getattr(self.idPelanggan, 'nama_pelanggan', 'Pelanggan')
        return f"Notifikasi untuk {pelanggan_nama}"