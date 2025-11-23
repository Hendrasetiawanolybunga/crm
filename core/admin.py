from django.contrib import admin

# Register your models here.
from . models import Pelanggan, Kategori, Produk, Transaksi, DetailTransaksi, Notifikasi, DiskonPelanggan
admin.site.register(Pelanggan)
admin.site.register(Kategori)
admin.site.register(Produk)
admin.site.register(Transaksi)
admin.site.register(DetailTransaksi)
admin.site.register(Notifikasi)
admin.site.register(DiskonPelanggan)