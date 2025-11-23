from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Pelanggan, Produk, Transaksi, DetailTransaksi
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

# Simple login_required decorator using session
from functools import wraps
from django.http import HttpResponseRedirect


def login_required(view_func):
	@wraps(view_func)
	def _wrapped(request, *args, **kwargs):
		if request.session.get('pelanggan_id'):
			try:
				pelanggan = Pelanggan.objects.get(pk=request.session.get('pelanggan_id'))
				request.pelanggan = pelanggan
			except Pelanggan.DoesNotExist:
				request.session.pop('pelanggan_id', None)
				return redirect('core:login')
			return view_func(request, *args, **kwargs)
		return redirect('core:login')
	return _wrapped


# Helper to format currency (simple)
def format_currency(value):
	try:
		return f"Rp{float(value):,.2f}"
	except Exception:
		return value


def home(request):
	products = Produk.objects.all()[:6]
	return render(request, 'core/home.html', {'products': products})


def products(request):
	products = Produk.objects.all()
	return render(request, 'core/products.html', {'products': products})


def register(request):
	message = ''
	if request.method == 'POST':
		nama = request.POST.get('nama_pelanggan')
		alamat = request.POST.get('alamat')
		tanggal_lahir = request.POST.get('tanggal_lahir')
		no_hp = request.POST.get('no_hp')
		username = request.POST.get('username')
		password = request.POST.get('password')
		email = request.POST.get('email')

		# basic validation
		if Pelanggan.objects.filter(username=username).exists():
			message = 'Username sudah terpakai.'
		elif Pelanggan.objects.filter(email=email).exists() and email:
			message = 'Email sudah terdaftar.'
		else:
			pel = Pelanggan.objects.create(
				nama_pelanggan=nama,
				alamat=alamat,
				tanggal_lahir=tanggal_lahir or None,
				no_hp=no_hp,
				username=username,
				email=email or None
			)
			# set hashed password
			pel.set_password(password)
			pel.save(update_fields=['password'])
			request.session['pelanggan_id'] = pel.id
			return redirect('core:home')

	return render(request, 'core/register.html', {'message': message})


def login_view(request):
	message = ''
	if request.method == 'POST':
		username = request.POST.get('username')
		password = request.POST.get('password')
		try:
			pel = Pelanggan.objects.get(username=username)
			if pel.check_password(password):
				request.session['pelanggan_id'] = pel.id
				return redirect('core:home')
			else:
				message = 'Password salah.'
		except Pelanggan.DoesNotExist:
			message = 'Akun tidak ditemukan.'
	return render(request, 'core/login.html', {'message': message})


def logout_view(request):
	request.session.pop('pelanggan_id', None)
	return redirect('core:home')


@login_required
def cart_view(request):
	# Cart is stored in session as list of dicts: {'product_id': id, 'qty': n}
	cart = request.session.get('cart', [])
	items = []
	subtotal = 0
	for entry in cart:
		try:
			p = Produk.objects.get(pk=entry.get('product_id'))
			qty = int(entry.get('qty', 1))
			total = float(p.harga_produk) * qty
			subtotal += total
			items.append({'product': p, 'qty': qty, 'total': total})
		except Produk.DoesNotExist:
			continue
	# Do not auto-calculate ongkir here; admin will set it later
	grand_total = subtotal
	return render(request, 'core/cart.html', {
		'items': items,
		'subtotal': subtotal,
		'grand_total': grand_total,
		'format_currency': format_currency,
	})


@login_required
def update_cart(request):
	if request.method == 'POST':
		cart = request.session.get('cart', [])
		# expecting fields like qty_<product_id>
		for entry in cart:
			pid = entry.get('product_id')
			field = f'qty_{pid}'
			if field in request.POST:
				try:
					q = int(request.POST.get(field, 1))
					if q < 1:
						q = 1
					entry['qty'] = q
				except Exception:
					continue
		request.session['cart'] = cart
	return redirect('core:cart')


@login_required
@require_POST
def add_to_cart(request, product_id):
	# Ensure product_id is treated as int for comparisons
	try:
		pid = int(product_id)
	except Exception:
		pid = product_id

	cart = request.session.get('cart', [])
	found = False
	for entry in cart:
		try:
			if int(entry.get('product_id')) == int(pid):
				entry['qty'] = int(entry.get('qty', 1)) + 1
				found = True
				break
		except Exception:
			continue
	if not found:
		cart.append({'product_id': pid, 'qty': 1})
	request.session['cart'] = cart

	# add success message and redirect back to referrer or products page
	try:
		messages.success(request, 'Produk berhasil ditambahkan ke keranjang.')
	except Exception:
		pass

	ref = request.META.get('HTTP_REFERER')
	if ref:
		return redirect(ref)
	return redirect('core:products')


@login_required
def remove_from_cart(request, product_id):
	cart = request.session.get('cart', [])
	cart = [e for e in cart if e.get('product_id') != product_id]
	request.session['cart'] = cart
	return redirect('core:cart')


@login_required
def account_manage(request):
	pel = request.pelanggan
	message = ''
	if request.method == 'POST':
		pel.nama_pelanggan = request.POST.get('nama_pelanggan')
		pel.alamat = request.POST.get('alamat')
		pel.tanggal_lahir = request.POST.get('tanggal_lahir') or pel.tanggal_lahir
		pel.no_hp = request.POST.get('no_hp')
		email = request.POST.get('email')
		if email:
			pel.email = email
		new_pass = request.POST.get('password')
		if new_pass:
			pel.set_password(new_pass)
		pel.save()
		message = 'Perubahan tersimpan.'
	return render(request, 'core/account_manage.html', {'pelanggan': pel, 'message': message})


@login_required
def order_history(request):
	pel = request.pelanggan
	orders = Transaksi.objects.filter(idPelanggan=pel).order_by('-tanggal')
	return render(request, 'core/order_history.html', {'orders': orders, 'format_currency': format_currency})


@login_required
def order_detail(request, order_id):
	pel = request.pelanggan
	order = get_object_or_404(Transaksi, pk=order_id)
	if order.idPelanggan.id != pel.id:
		return redirect('core:order_history')
	items = DetailTransaksi.objects.filter(idTransaksi=order)
	can_feedback = (order.status_transaksi == 'SELESAI') and (not order.feedback)
	return render(request, 'core/order_detail.html', {
		'order': order,
		'items': items,
		'can_feedback': can_feedback,
		'format_currency': format_currency,
	})


@login_required
def submit_feedback(request, order_id):
	pel = request.pelanggan
	order = get_object_or_404(Transaksi, pk=order_id)
	if order.idPelanggan.id != pel.id:
		return redirect('core:order_history')
	if request.method == 'POST' and order.status_transaksi == 'SELESAI' and not order.feedback:
		feedback = request.POST.get('feedback')
		order.feedback = feedback
		if request.FILES.get('fotofeedback'):
			file = request.FILES['fotofeedback']
			# Save file using default storage
			path = default_storage.save('feedback_images/' + file.name, ContentFile(file.read()))
			order.fotofeedback = path
		order.save()
	return redirect('core:order_detail', order_id=order.id)


@login_required
def checkout(request):
	pel = request.pelanggan
	cart = request.session.get('cart', [])
	if not cart:
		return redirect('core:cart')

	items = []
	subtotal = 0
	for entry in cart:
		try:
			p = Produk.objects.get(pk=entry.get('product_id'))
			qty = int(entry.get('qty', 1))
			total = float(p.harga_produk) * qty
			subtotal += total
			items.append({'product': p, 'qty': qty, 'total': total})
		except Produk.DoesNotExist:
			continue

	if request.method == 'POST':
		alamat = request.POST.get('alamat_pengiriman')
		# create transaksi and save uploaded bukti if provided
		now = timezone.now()
		batas = now + timezone.timedelta(hours=24)
		transaksi = Transaksi.objects.create(
			tanggal=now,
			total=subtotal,
			ongkir=0,
			status_transaksi='MENUNGGU_VERIFIKASI_PEMBAYARAN',
			idPelanggan=pel,
			alamat_pengiriman=alamat,
			waktu_checkout=now,
			batas_waktu_bayar=batas
		)
		# create detail transaksi
		for it in items:
			DetailTransaksi.objects.create(
				idTransaksi=transaksi,
				idProduk=it['product'],
				jumlah_produk=it['qty'],
				sub_total=it['total']
			)

		# handle uploaded bukti_bayar
		if request.FILES.get('bukti_bayar'):
			f = request.FILES['bukti_bayar']
			path = default_storage.save('bukti_pembayaran/' + f.name, ContentFile(f.read()))
			transaksi.bukti_bayar = path
			transaksi.save()

		# clear cart
		request.session['cart'] = []
		# redirect to order detail where countdown is shown
		return redirect('core:order_detail', order_id=transaksi.id)

	# For GET, provide a 24-hour preview deadline for the checkout page countdown
	preview_deadline = timezone.now() + timezone.timedelta(hours=24)
	return render(request, 'core/payment_address.html', {'items': items, 'subtotal': subtotal, 'checkout_deadline': preview_deadline})


@login_required
def payment_upload(request, order_id):
	pel = request.pelanggan
	order = get_object_or_404(Transaksi, pk=order_id)
	if order.idPelanggan.id != pel.id:
		return redirect('core:order_history')

	if request.method == 'POST' and request.FILES.get('bukti'):
		f = request.FILES['bukti']
		path = default_storage.save('bukti_pembayaran/' + f.name, ContentFile(f.read()))
		order.bukti_bayar = path
		# set status to waiting verification
		order.status_transaksi = 'MENUNGGU VERIFIKASI'
		order.save()
		return redirect('core:order_detail', order_id=order.id)

	return render(request, 'core/payment_upload.html', {'order': order, 'format_currency': format_currency})


def product_detail(request, product_id):
	p = get_object_or_404(Produk, pk=product_id)
	return render(request, 'core/product_detail.html', {'product': p})
