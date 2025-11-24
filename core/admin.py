from django.contrib import admin
from django.db.models import Sum, F
from django.utils import timezone
from django.urls import path
from django.shortcuts import render
from django.db.models.functions import TruncMonth
from django.template.response import TemplateResponse
import json
from decimal import Decimal
from .models import Pelanggan, Kategori, Produk, Transaksi, DetailTransaksi, Notifikasi, DiskonPelanggan

# Status yang dihitung sebagai revenue
REVENUE_STATUSES = ['DIBAYAR', 'DIKIRIM', 'SELESAI']

# Helper function to format Rupiah
def format_rupiah(amount):
    """Format a number as Indonesian Rupiah string: Rp10.000,00"""
    try:
        amount = float(amount)
    except Exception:
        return amount
        
    # Baris yang dikoreksi: Pastikan spasi di sini adalah spasi normal (karakter ASCII 0x20)
    s = f"{amount:,.2f}" # e.g. '10,000.00'
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"Rp{s}"

# Inline Admin for DetailTransaksi
class DetailTransaksiInline(admin.TabularInline):
    model = DetailTransaksi
    extra = 1
    fields = ('idProduk', 'jumlah_produk', 'sub_total')
    readonly_fields = ('sub_total',)

# Custom Admin Site
class PenjualanAdminSite(admin.AdminSite):
    site_header = "Barokah Admin"
    site_title = "Barokah Admin Portal"
    index_title = "Selamat Datang di Dashboard Admin Barokah"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('', self.admin_view(self.index), name='index'),
        ]
        return custom_urls + urls

    def index(self, request, extra_context=None):
        # Call the parent index method to get the default context with app list
        context = super().index(request, extra_context)
        
        # If context is a TemplateResponse, extract its context_data
        if hasattr(context, 'context_data'):
            context_dict = context.context_data
        else:
            context_dict = dict(context) if context else {}
            
        # --- 1. Calculate Metrics ---
        total_customers = Pelanggan.objects.count()
        total_products = Produk.objects.count()
        
        # Transaksi Sukses
        total_successful_transactions = Transaksi.objects.filter(status_transaksi__in=REVENUE_STATUSES).count()
        
        # Total Revenue Global (Menggunakan Transaksi.total)
        total_revenue_result = Transaksi.objects.filter(status_transaksi__in=REVENUE_STATUSES).aggregate(total=Sum('total'))
        # KRITIS: Pastikan menggunakan float()
        total_revenue = float(total_revenue_result['total'] or 0)
        formatted_total_revenue = format_rupiah(total_revenue)
        
        # --- 2. Monthly Revenue Data for Chart ---
        
        monthly_revenue_data = (
            Transaksi.objects
            .filter(status_transaksi__in=REVENUE_STATUSES) # Gunakan semua status revenue
            .annotate(month=TruncMonth('tanggal'))
            .values('month')
            .annotate(total=Sum('total'))
            .order_by('month')
        )
        
        # Prepare chart data as a single table format for Google Charts with Date objects
        # Header: Tipe Kolom 0 = Date, Kolom 1 = Number
        chart_data_table = [['Date', 'Pendapatan (Rp)']]
        
        for entry in monthly_revenue_data:
            month_date = entry['month']  # Should be a datetime.date/datetime.datetime object
            revenue_value = entry['total'] or 0
            
            # KRITIS: Pastikan Revenue adalah float murni
            chart_data_table.append([month_date, float(revenue_value)])
        
        # Add our custom metrics to the context
        context_dict.update({
            'total_customers': total_customers,
            'total_products': total_products,
            'total_successful_transactions': total_successful_transactions,
            'total_revenue': formatted_total_revenue,  # Ini untuk card
            'chart_data_table': chart_data_table,  # Ini untuk json_script di template
        })
        
        # If we had a TemplateResponse, return it with updated context
        if hasattr(context, 'context_data'):
            context.context_data = context_dict
            return context
        else:
            return TemplateResponse(request, 'admin/index.html', context_dict)

# Custom Admin for Pelanggan model
class PelangganAdmin(admin.ModelAdmin):
    list_display = ('id', 'nama_pelanggan', 'email', 'no_hp', 'total_riwayat_belanja')
    list_filter = ('is_birthday_discount_active',)
    search_fields = ('nama_pelanggan', 'email', 'no_hp')
    list_per_page = 5
    list_max_show_all = 500
    list_display_links = ('id', 'nama_pelanggan')

# Custom Admin for Kategori model
class KategoriAdmin(admin.ModelAdmin):
    list_display = ('id', 'nama_kategori')
    search_fields = ('nama_kategori',)
    list_per_page = 5
    list_max_show_all = 500
    list_display_links = ('id', 'nama_kategori')

# Custom Admin for Produk model
class ProdukAdmin(admin.ModelAdmin):
    list_display = ('id', 'nama_produk', 'kategori', 'harga_produk', 'stok_produk')
    list_filter = ('kategori',)
    search_fields = ('nama_produk', 'deskripsi_produk')
    list_editable = ('stok_produk', 'harga_produk')
    list_per_page = 5
    list_max_show_all = 500
    list_display_links = ('id', 'nama_produk')

# Custom Admin for Transaksi model
class TransaksiAdmin(admin.ModelAdmin):
    list_display = ('id', 'idPelanggan', 'display_tanggal', 'display_total', 'status_transaksi')
    list_filter = ('status_transaksi', 'tanggal')
    search_fields = ('id', 'idPelanggan__nama_pelanggan')
    readonly_fields = ('waktu_checkout', 'batas_waktu_bayar')
    inlines = [DetailTransaksiInline]
    list_per_page = 5
    list_max_show_all = 500
    list_display_links = ('id',)
    
    @admin.display(description='Tanggal Transaksi')
    def display_tanggal(self, obj):
        return obj.tanggal.strftime("%d %B %Y, %H:%M")
    
    @admin.display(description='Total')
    def display_total(self, obj):
        # Menggunakan helper format_rupiah
        return format_rupiah(obj.total)
    
    def save_formset(self, request, form, formset, change):
        # Save the formset first
        instances = formset.save(commit=False)
        
        # Save each instance and calculate sub_total
        for instance in instances:
            if isinstance(instance, DetailTransaksi):
                # Calculate sub_total if both idProduk and jumlah_produk are available
                if instance.idProduk and instance.jumlah_produk:
                    instance.sub_total = instance.idProduk.harga_produk * instance.jumlah_produk
                instance.save()
        
        # Delete instances marked for deletion
        for obj in formset.deleted_objects:
            obj.delete()
        
        formset.save_m2m()
        
        # After saving all DetailTransaksi items, recalculate the Transaksi total
        transaksi = form.instance
        if transaksi.pk:  # Only if the Transaksi object has been saved
            detail_transaksi_items = DetailTransaksi.objects.filter(idTransaksi=transaksi)
            total = sum(item.sub_total or 0 for item in detail_transaksi_items)
            transaksi.total = total
            transaksi.save(update_fields=['total'])

# Custom Admin for Notifikasi model
class NotifikasiAdmin(admin.ModelAdmin):
    list_display = ('id', 'idPelanggan', 'tipe_pesan', 'is_read', 'created_at')
    list_filter = ('tipe_pesan', 'is_read', 'created_at')
    search_fields = ('idPelanggan__nama_pelanggan', 'isi_pesan')
    list_per_page = 5
    list_max_show_all = 500
    list_display_links = ('id',)

# Custom Admin for DiskonPelanggan model
class DiskonPelangganAdmin(admin.ModelAdmin):
    list_display = ('id', 'idPelanggan', 'idProduk', 'persen_diskon', 'status')
    list_filter = ('status', 'persen_diskon')
    search_fields = ('idPelanggan__nama_pelanggan', 'idProduk__nama_produk')
    list_per_page = 5
    list_max_show_all = 500
    list_display_links = ('id',)

# Create custom admin site instance
penjualan_admin_site = PenjualanAdminSite(name='penjualan_admin')

# Register models with custom admin
penjualan_admin_site.register(Pelanggan, PelangganAdmin)
penjualan_admin_site.register(Kategori, KategoriAdmin)
penjualan_admin_site.register(Produk, ProdukAdmin)
penjualan_admin_site.register(Transaksi, TransaksiAdmin)
penjualan_admin_site.register(Notifikasi, NotifikasiAdmin)
penjualan_admin_site.register(DiskonPelanggan, DiskonPelangganAdmin)