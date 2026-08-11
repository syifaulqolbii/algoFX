# Capture traffic ke port 8080 (bridge) dari VPS Linux.
# Membantu debug apakah EA MT5 mengirim request.
# Jalankan di background VPS, lalu tunggu ~5 menit, lalu Ctrl+C dan inspect.

echo "Mengecek apakah tcpdump tersedia..."
if ! command -v tcpdump >/dev/null 2>&1; then
  echo "tcpdump tidak tersedia. Install dengan: sudo apt install -y tcpdump"
  exit 1
fi

echo "Mendeteksi interface Tailscale..."
IFACE=$(ip -4 -o addr show | awk '/tailscale/ {print $2}' | head -1)
if [ -z "$IFACE" ]; then
  IFACE=$(ip -4 -o addr show | awk '$2 != "lo" && $2 !~ /^docker/ && $2 !~ /^br-/ {print $2}' | head -1)
  echo "Tailscale interface tidak ditemukan, fallback ke: $IFACE"
fi

echo "Menangkap traffic ke port 8080 di $IFACE selama 5 menit..."
sudo timeout 300 tcpdump -i "$IFACE" -nn -s0 -A 'port 8080' 2>&1 | grep -E "POST|GET|Host:|HTTP/1\.1|200 OK|401|404" | head -40