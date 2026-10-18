# S4-IRMC-Tool

Eigene Lüfterkurve für Fujitsu PRIMERGY mit iRMC S4. Getestet auf RX2520 M1 (`gm-pve-02`, Proxmox, iRMC 9.08F, BIOS R1.21.0).

## Wie es funktioniert

Die iRMC S4 hat dafür keinen dokumentierten Befehl. Gefunden wurde ein Fujitsu-OEM-Befehl, der auf diesem Gerät so wirkt:

| Befehl | Wirkung |
|---|---|
| `ipmitool raw 0x2e 0xf5 0x80 0x28 0x00 0x2d 0x46 0x57 0x01 0xff 0x80 <pct>` | alle Lüfter auf `<pct>` % (0x0a ≈ 1500 U/min, 0x28 ≈ 6100 U/min) |
| `ipmitool raw 0x2e 0xf5 0x80 0x28 0x00 0x2d 0x46 0x57 0x01 0xff 0x00 0x00` | zurück auf iRMC-Automatik (≈ 22 %, 3300 U/min) |

Die Netzteillüfter regeln unterhalb von etwa 3500 U/min selbst.

`irmc-fan.py` liest alle 10 s Temperaturen: CPU, RAM und Umgebung über `ipmitool sdr`, die Platten hinter dem MegaRAID über `smartctl`. Aus diesen Werten setzt es die Lüfter nach den Kurven im Kopf der Datei. Für jede Gruppe gilt eine eigene Kurve, eingestellt wird der höchste Wert. Der Mindestwert liegt bei 12 %, weil der RAID-Controller keinen eigenen Sensor hat.

**Wann es auf die Automatik zurückschaltet:** wenn keine CPU-Werte kommen, bei einer Grenztemperatur (Werte in `LIMITS`), bei einem Fehler beim Setzen und wenn der Dienst beendet wird.

## Installation auf dem Host

```
apt install -y ipmitool smartmontools git
git clone https://github.com/gm-itsolutions/s4-irmc-tool /opt/s4-irmc-tool
python3 /opt/s4-irmc-tool/irmc-fan.py --selftest
python3 /opt/s4-irmc-tool/irmc-fan.py --dry-run      # Strg+C nach ein paar Zeilen
cp /opt/s4-irmc-tool/irmc-fan.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now irmc-fan
journalctl -u irmc-fan -f
```

## Notfall

```
systemctl stop irmc-fan
python3 /opt/s4-irmc-tool/irmc-fan.py --auto
```

Ein Neustart der iRMC bringt die Automatik vermutlich ebenfalls zurück. Geprüft ist das nicht.
