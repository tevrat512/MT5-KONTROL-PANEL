import sys
import os
import json
import threading
import time
import asyncio
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
import MetaTrader5 as mt5
from telethon import TelegramClient, events
import re

def resource_path(relative_path):
    """Gömülü dosyaların yolunu bul (PyInstaller için)"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- KONUM AYARLARI (EXE'nin yanındaki TELEGRAM klasörü) ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, 'TELEGRAM')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

SESSION_PATH = os.path.join(DATA_DIR, 'altin_bot_oturum')
SETTINGS_FILE = os.path.join(DATA_DIR, 'ayarlar.json')
STATS_FILE = os.path.join(DATA_DIR, 'gunluk_veri.json')
LOG_FILE = os.path.join(DATA_DIR, f'{datetime.now().strftime("%Y-%m-%d")}_log.txt')

# --- TELEGRAM API ---
API_ID =ID'nizi buraya yazın
API_HASH = 'hash ı buraya yazın'
HEDEF_GRUP_ID = grup ıd yı buraya yazın

# --- MT5 DEĞİŞKENLERİ ---
mt5_baglanti = False
oto_trade_aktif = False
telegram_client = None
telegram_thread = None

# --- GÜNLÜK VERİLER (kalıcı) ---
gunluk_veri = {
    "tarih": datetime.now().strftime("%Y-%m-%d"),
    "baslangic_bakiye": 0.0,
    "toplam_kar_kapandi": 0.0,
    "buy_adet": 0,
    "buy_kar_kapandi": 0.0,
    "sell_adet": 0,
    "sell_kar_kapandi": 0.0
}

# --- AYARLAR ---
ayarlar = {
    "lot": 0.10,
    "islem_adedi": 2,
    "parite": "GOLDm#"
}

# --- VERİ YÖNETİMİ ---
def veri_yukle():
    global gunluk_veri
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, 'r', encoding='utf-8') as f:
                kayit = json.load(f)
                if kayit.get("tarih") == datetime.now().strftime("%Y-%m-%d"):
                    gunluk_veri = kayit
                    log_yaz("Önceki veriler yüklendi, kaldığı yerden devam.")
                else:
                    log_yaz("Yeni gün, istatistikler sıfırlandı.")
        except Exception as e:
            log_yaz(f"Veri yüklenirken hata: {e}")

def veri_kaydet():
    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(gunluk_veri, f, indent=4, ensure_ascii=False)

def ayarlari_yukle():
    global ayarlar
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            ayarlar.update(json.load(f))

def ayarlari_kaydet():
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(ayarlar, f, indent=4)

def log_yaz(mesaj):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {mesaj}\n")

# --- MT5 İŞLEM AÇ ---
def islem_ac(side, hedef_pip):
    if not mt5_baglanti:
        log_yaz("MT5 bağlı değil, işlem açılamadı")
        return

    symbol = ayarlar["parite"]
    lot = ayarlar["lot"]
    net_tp_pip = hedef_pip - 1
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        log_yaz(f"HATA: {symbol} fiyatı alınamadı")
        return

    if side == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        tp_price = price + (net_tp_pip * 0.01)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        tp_price = price - (net_tp_pip * 0.01)

    islem_adedi = ayarlar["islem_adedi"]
    for i in range(islem_adedi):
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "tp": round(tp_price, 1),
            "magic": 2026,
            "comment": f"Tevrat{i+1}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            log_yaz(f"İşlem açıldı: {side.upper()} {lot} lot TP:{round(tp_price,1)}")
        else:
            log_yaz(f"İşlem hatası: {result.retcode} - {result.comment}")

    acik_islemleri_guncelle_tablo()

# --- AÇIK İŞLEMLER ---
def acik_islemleri_al():
    if not mt5_baglanti:
        return []
    pozisyonlar = mt5.positions_get()
    return pozisyonlar if pozisyonlar is not None else []

def acik_islemleri_guncelle_tablo():
    global tablo
    for row in tablo.get_children():
        tablo.delete(row)
    for pos in acik_islemleri_al():
        if pos.symbol == ayarlar["parite"]:
            tablo.insert("", "end", values=(
                pos.symbol,
                "BUY" if pos.type == 0 else "SELL",
                pos.volume,
                1,
                f"{pos.profit:.2f}",
                pos.comment
            ))

# --- ÖZET BİLGİLERİ GÜNCELLE (GUI) ---
def ozet_bilgileri_guncelle():
    if not mt5_baglanti:
        etiket_mt5_durum.config(text="BAĞLI DEĞİL", foreground="red")
        return

    etiket_mt5_durum.config(text="BAĞLI", foreground="green")
    hesap = mt5.account_info()
    if hesap:
        if gunluk_veri["baslangic_bakiye"] == 0:
            gunluk_veri["baslangic_bakiye"] = hesap.balance
            veri_kaydet()
            log_yaz(f"Gün başı bakiye kaydedildi: {gunluk_veri['baslangic_bakiye']}")

        etiket_bakiye.config(text=f"{gunluk_veri['baslangic_bakiye']:.2f}")

        pozlar = acik_islemleri_al()
        net_kar = sum(p.profit for p in pozlar)
        if net_kar > 0:
            etiket_net_kar.config(text=f"{net_kar:.2f}", foreground="lime green")
        elif net_kar < 0:
            etiket_net_kar.config(text=f"{net_kar:.2f}", foreground="red")
        else:
            etiket_net_kar.config(text=f"{net_kar:.2f}", foreground="white")

        toplam_kar = hesap.balance - gunluk_veri["baslangic_bakiye"]
        if toplam_kar > 0:
            etiket_toplam_kar.config(text=f"{toplam_kar:.2f}", foreground="lime green")
        elif toplam_kar < 0:
            etiket_toplam_kar.config(text=f"{toplam_kar:.2f}", foreground="red")
        else:
            etiket_toplam_kar.config(text=f"{toplam_kar:.2f}", foreground="dodger blue")

        etiket_toplam_bakiye.config(text=f"{hesap.balance:.2f}")

        etiket_buy_adet.config(text=str(gunluk_veri["buy_adet"]))
        etiket_buy_kar.config(text=f"{gunluk_veri['buy_kar_kapandi']:.2f}")
        etiket_sell_adet.config(text=str(gunluk_veri["sell_adet"]))
        etiket_sell_kar.config(text=f"{gunluk_veri['sell_kar_kapandi']:.2f}")

# --- KAPANAN İŞLEMLERİ TAKİP ---
def kapanan_islem_takip():
    onceki_pos_idleri = set()
    while True:
        if mt5_baglanti:
            pozlar = acik_islemleri_al()
            simdiki_idler = {p.ticket for p in pozlar}
            kapanan_idler = onceki_pos_idleri - simdiki_idler

            for pid in kapanan_idler:
                deals = mt5.history_deals_get(position=pid)
                if deals and len(deals) > 0:
                    kar = sum(d.profit for d in deals)
                    is_buy = False
                    for d in deals:
                        if d.entry == 0:
                            if d.type == 0:
                                is_buy = True
                            break
                    if is_buy:
                        gunluk_veri["buy_adet"] += 1
                        gunluk_veri["buy_kar_kapandi"] += kar
                    else:
                        gunluk_veri["sell_adet"] += 1
                        gunluk_veri["sell_kar_kapandi"] += kar
                    gunluk_veri["toplam_kar_kapandi"] += kar
                    log_yaz(f"İşlem kapandı (pos_id={pid}) Kar: {kar:.2f}")
                    veri_kaydet()
            onceki_pos_idleri = simdiki_idler
        time.sleep(2)

# --- TELEGRAM DİNLEYİCİ ---
async def telegram_dinleyici():
    global telegram_client
    telegram_client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await telegram_client.start()
    print("Telegram dinleyici başladı")

    @telegram_client.on(events.NewMessage(chats=HEDEF_GRUP_ID))
    async def handler(event):
        if not oto_trade_aktif:
            return
        mesaj = event.raw_text.upper()
        if "HEDEFTE" in mesaj or "✅" in mesaj or "DONE" in mesaj:
            return
        if "XAU USD" in mesaj and "GİR" in mesaj:
            pips = re.findall(r'HEDEF (\d+)', mesaj)
            if not pips:
                return
            hedef = int(pips[0])
            if "LONG" in mesaj:
                log_yaz(f"Sinyal LONG hedef {hedef}")
                islem_ac("buy", hedef)
            elif "SELL" in mesaj or "SHORT" in mesaj:
                log_yaz(f"Sinyal SELL hedef {hedef}")
                islem_ac("sell", hedef)

    await telegram_client.run_until_disconnected()

def telegram_baslat():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(telegram_dinleyici())

# --- MT5 BAĞLAN / KES ---
def mt5_baglan_toggle():
    global mt5_baglanti
    if not mt5_baglanti:
        if mt5.initialize():
            mt5_baglanti = True
            log_yaz("MT5 bağlandı")
            ozet_bilgileri_guncelle()
            acik_islemleri_guncelle_tablo()
            btn_mt5.config(text="MT5 Bağlantıyı Kes")
        else:
            mt5_baglanti = False
            messagebox.showerror("Hata", f"MT5 bağlanamadı: {mt5.last_error()}")
    else:
        mt5.shutdown()
        mt5_baglanti = False
        log_yaz("MT5 bağlantısı kesildi")
        ozet_bilgileri_guncelle()
        btn_mt5.config(text="MT5 Bağlan")
        for row in tablo.get_children():
            tablo.delete(row)

# --- OTO TRADE BAŞLAT / DURDUR ---
def oto_trade_toggle():
    global oto_trade_aktif, telegram_thread
    if not mt5_baglanti:
        messagebox.showwarning("Uyarı", "Önce MT5 bağlanın")
        return

    if not oto_trade_aktif:
        oto_trade_aktif = True
        if telegram_thread is None or not telegram_thread.is_alive():
            telegram_thread = threading.Thread(target=telegram_baslat, daemon=True)
            telegram_thread.start()
        btn_ototrade.config(text="Oto Trade'i Durdur")
        etiket_ototrade_durum.config(text="Oto Trade: AKTİF", foreground="lime green")
        log_yaz("Oto trade başlatıldı")
        messagebox.showinfo("Bilgi", "Oto trade aktif, sinyaller işlenecek")
    else:
        oto_trade_aktif = False
        btn_ototrade.config(text="Oto Trade'i Başlat")
        etiket_ototrade_durum.config(text="Oto Trade: KAPALI", foreground="red")
        log_yaz("Oto trade durduruldu")
        messagebox.showinfo("Bilgi", "Oto trade durduruldu")

def ayarlari_kaydet_ve_uygula():
    ayarlar["lot"] = float(giris_lot.get())
    ayarlar["islem_adedi"] = int(giris_adet.get())
    ayarlar["parite"] = giris_parite.get()
    ayarlari_kaydet()
    messagebox.showinfo("Kaydedildi", "Ayarlar kaydedildi")

# --- ANA GUI (Splash ekranı ile birlikte) ---
def main():
    global ana_pencere, tablo, giris_lot, giris_adet, giris_parite
    global btn_mt5, etiket_mt5_durum, btn_ototrade, etiket_ototrade_durum
    global etiket_bakiye, etiket_net_kar, etiket_toplam_kar, etiket_toplam_bakiye
    global etiket_buy_adet, etiket_buy_kar, etiket_sell_adet, etiket_sell_kar

    ana_pencere = tk.Tk()
    ana_pencere.title("SEYİR 7 OTO TİCARET")
    ana_pencere.geometry("900x700")
    ana_pencere.minsize(700, 600)
    ana_pencere.resizable(True, True)

    # Sol üst ikon (EXE içinden)
    icon_path = resource_path('exeiconn.ico')
    if os.path.exists(icon_path):
        try:
            ana_pencere.iconbitmap(icon_path)
        except:
            pass

    # --- SPLASH FRAME (geçici) ---
    splash_frame = tk.Frame(ana_pencere, bg='black')
    splash_frame.pack(fill=tk.BOTH, expand=True)

    # Resmi yükleyip canvas'a yerleştir (EXE içinden)
    img_path = resource_path('gres.PNG')
    canvas = tk.Canvas(splash_frame, bg='black', highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    splash_img = None
    window_width = 900
    window_height = 700

    try:
        pil_img = Image.open(img_path)
        img_ratio = pil_img.width / pil_img.height
        window_ratio = window_width / window_height
        if img_ratio > window_ratio:
            new_width = window_width
            new_height = int(window_width / img_ratio)
        else:
            new_height = window_height
            new_width = int(window_height * img_ratio)
        pil_img = pil_img.resize((new_width, new_height), Image.LANCZOS)
        splash_img = ImageTk.PhotoImage(pil_img)
        canvas.create_image(window_width//2, window_height//2, image=splash_img, anchor='center', tags="img")
    except Exception as e:
        canvas.create_text(window_width//2, window_height//2, text=f"RESİM YÜKLENEMEDİ: {e}", fill='white', font=("Arial", 12), tags="text")

    # Yazıları canvas üzerine bindir
    text_item = canvas.create_text(window_width//2, window_height//2 + 200, text="", fill='white', font=("Arial", 14, "bold"), justify='center', tags="splash_text")

    def update_splash_text(text, duration):
        canvas.itemconfig(text_item, text=text)
        splash_frame.update()
        time.sleep(duration)

    def show_splash_sequence():
        update_splash_text("SEYİR 7 OTO TRADİNG e hoşgeldınız", 1.1)
        update_splash_text("BAŞARININ BEDELİNİ BİR DÖNEM ÖDEMEYENLER\nBAŞARISIZLIĞIN BEDELİNİ ÖMÜR BOYU ÖDER", 2.0)
        update_splash_text("AÇILIYOR", 0.5)
        splash_frame.destroy()
        create_main_gui()

    threading.Thread(target=show_splash_sequence, daemon=True).start()
    ana_pencere.mainloop()

def create_main_gui():
    global tablo, giris_lot, giris_adet, giris_parite
    global btn_mt5, etiket_mt5_durum, btn_ototrade, etiket_ototrade_durum
    global etiket_bakiye, etiket_net_kar, etiket_toplam_kar, etiket_toplam_bakiye
    global etiket_buy_adet, etiket_buy_kar, etiket_sell_adet, etiket_sell_kar

    main_paned = tk.PanedWindow(ana_pencere, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, sashwidth=5)
    main_paned.pack(fill=tk.BOTH, expand=True)

    # ----- SOL PANEL (Ayarlar) -----
    sol_frame = tk.LabelFrame(main_paned, text="Bot Ayarları", padx=5, pady=5)
    main_paned.add(sol_frame, width=180, minsize=150)

    tk.Label(sol_frame, text="Lot Miktarı:").pack(anchor="w", pady=2)
    giris_lot = tk.Entry(sol_frame)
    giris_lot.insert(0, str(ayarlar["lot"]))
    giris_lot.pack(fill=tk.X, pady=2)

    tk.Label(sol_frame, text="İşlem Adedi:").pack(anchor="w", pady=2)
    giris_adet = tk.Entry(sol_frame)
    giris_adet.insert(0, str(ayarlar["islem_adedi"]))
    giris_adet.pack(fill=tk.X, pady=2)

    tk.Label(sol_frame, text="Parite:").pack(anchor="w", pady=2)
    giris_parite = tk.Entry(sol_frame)
    giris_parite.insert(0, ayarlar["parite"])
    giris_parite.pack(fill=tk.X, pady=2)

    btn_kaydet = tk.Button(sol_frame, text="Kaydet", command=ayarlari_kaydet_ve_uygula, bg="orange")
    btn_kaydet.pack(pady=10)

    # Sağ taraftaki içerik
    sag_container = tk.Frame(main_paned)
    main_paned.add(sag_container, width=700, minsize=500)

    # ----- ÜST BÖLÜM (Kontrol ve Özet) -----
    ust_frame = tk.Frame(sag_container)
    ust_frame.pack(fill=tk.X, padx=5, pady=5)

    kontrol_frame = tk.LabelFrame(ust_frame, text="Kontrol", padx=5, pady=5)
    kontrol_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5)

    mt5_cerceve = tk.Frame(kontrol_frame)
    mt5_cerceve.pack(side=tk.LEFT, padx=5)
    btn_mt5 = tk.Button(mt5_cerceve, text="MT5 Bağlan", command=mt5_baglan_toggle, bg="lightblue")
    btn_mt5.pack()
    etiket_mt5_durum = tk.Label(mt5_cerceve, text="BAĞLI DEĞİL", fg="red")
    etiket_mt5_durum.pack()

    ototrade_cerceve = tk.Frame(kontrol_frame)
    ototrade_cerceve.pack(side=tk.LEFT, padx=5)
    btn_ototrade = tk.Button(ototrade_cerceve, text="Oto Trade'i Başlat", command=oto_trade_toggle, bg="lightgreen")
    btn_ototrade.pack()
    etiket_ototrade_durum = tk.Label(ototrade_cerceve, text="Oto Trade: KAPALI", fg="red")
    etiket_ototrade_durum.pack()

    ozet_frame = tk.LabelFrame(ust_frame, text="Günlük Özet", padx=5, pady=5)
    ozet_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

    sol_ozet = tk.Frame(ozet_frame)
    sol_ozet.pack(side=tk.LEFT, padx=5, fill=tk.BOTH, expand=True)
    sol_ozet.grid_columnconfigure(0, weight=1)
    sol_ozet.grid_columnconfigure(1, weight=1)

    tk.Label(sol_ozet, text="Başlangıç Bakiyesi:").grid(row=0, column=0, sticky="e", padx=2)
    etiket_bakiye = tk.Label(sol_ozet, text="0.00")
    etiket_bakiye.grid(row=0, column=1, sticky="w")
    tk.Label(sol_ozet, text="Net Kar/Zarar (açık):").grid(row=1, column=0, sticky="e", padx=2)
    etiket_net_kar = tk.Label(sol_ozet, text="0.00")
    etiket_net_kar.grid(row=1, column=1, sticky="w")
    tk.Label(sol_ozet, text="Toplam Kar/zarar (kapanan):").grid(row=2, column=0, sticky="e", padx=2)
    etiket_toplam_kar = tk.Label(sol_ozet, text="0.00")
    etiket_toplam_kar.grid(row=2, column=1, sticky="w")
    tk.Label(sol_ozet, text="Toplam Bakiye:").grid(row=3, column=0, sticky="e", padx=2)
    etiket_toplam_bakiye = tk.Label(sol_ozet, text="0.00")
    etiket_toplam_bakiye.grid(row=3, column=1, sticky="w")

    sag_ozet = tk.Frame(ozet_frame)
    sag_ozet.pack(side=tk.RIGHT, padx=5, fill=tk.BOTH, expand=True)
    sag_ozet.grid_columnconfigure(0, weight=1)
    sag_ozet.grid_columnconfigure(1, weight=1)

    tk.Label(sag_ozet, text="Toplam Buy Adet:").grid(row=0, column=0, sticky="e", padx=2)
    etiket_buy_adet = tk.Label(sag_ozet, text="0")
    etiket_buy_adet.grid(row=0, column=1, sticky="w")
    tk.Label(sag_ozet, text="Buy Kar/Zarar:").grid(row=1, column=0, sticky="e", padx=2)
    etiket_buy_kar = tk.Label(sag_ozet, text="0.00")
    etiket_buy_kar.grid(row=1, column=1, sticky="w")
    tk.Label(sag_ozet, text="Toplam Sell Adet:").grid(row=2, column=0, sticky="e", padx=2)
    etiket_sell_adet = tk.Label(sag_ozet, text="0")
    etiket_sell_adet.grid(row=2, column=1, sticky="w")
    tk.Label(sag_ozet, text="Sell Kar/Zarar:").grid(row=3, column=0, sticky="e", padx=2)
    etiket_sell_kar = tk.Label(sag_ozet, text="0.00")
    etiket_sell_kar.grid(row=3, column=1, sticky="w")

    # ----- ALT BÖLÜM (Açık İşlemler) -----
    alt_frame = tk.LabelFrame(sag_container, text="Açık İşlemler", padx=5, pady=5)
    alt_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, padx=5, pady=5)

    columns = ("Parite", "Tür", "Lot", "Adet", "Anlık Kar/Zarar", "Yorum")
    tablo = ttk.Treeview(alt_frame, columns=columns, show="headings")

    tablo.heading("Parite", text="Parite")
    tablo.column("Parite", width=70, anchor="center", stretch=False)
    tablo.heading("Tür", text="Tür")
    tablo.column("Tür", width=50, anchor="center", stretch=False)
    tablo.heading("Lot", text="Lot")
    tablo.column("Lot", width=60, anchor="center", stretch=False)
    tablo.heading("Adet", text="Adet")
    tablo.column("Adet", width=50, anchor="center", stretch=False)
    tablo.heading("Anlık Kar/Zarar", text="Anlık Kar/Zarar")
    tablo.column("Anlık Kar/Zarar", width=120, anchor="center", stretch=True)
    tablo.heading("Yorum", text="Yorum")
    tablo.column("Yorum", width=150, anchor="w", stretch=True)

    scrollbar = ttk.Scrollbar(alt_frame, orient=tk.VERTICAL, command=tablo.yview)
    tablo.configure(yscrollcommand=scrollbar.set)
    tablo.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # --- PERİYODİK GÜNCELLEME ---
    def periyodik_guncelle():
        if mt5_baglanti:
            ozet_bilgileri_guncelle()
            acik_islemleri_guncelle_tablo()
        ana_pencere.after(2000, periyodik_guncelle)

    # --- BAŞLAT ---
    ayarlari_yukle()
    veri_yukle()
    threading.Thread(target=kapanan_islem_takip, daemon=True).start()
    periyodik_guncelle()

if __name__ == "__main__":
    main()