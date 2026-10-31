from scanner.wifi import parse_netsh_connected, parse_netsh_networks, parse_nmcli, pct_to_dbm

NETSH = """
Interface name : Wi-Fi
There are 3 networks currently visible.

SSID 1 : CampusNet
    Network type            : Infrastructure
    Authentication          : WPA2-Enterprise
    Encryption              : CCMP
    BSSID 1                 : AA:BB:CC:DD:EE:01
         Signal             : 80%
         Radio type         : 802.11ac
         Channel            : 36
    BSSID 2                 : aa:bb:cc:dd:ee:02
         Signal             : 44%
         Radio type         : 802.11n
         Channel            : 6

SSID 2 :
    Network type            : Infrastructure
    BSSID 1                 : 11:22:33:44:55:66
         Signal             : 20%
"""

IFACES = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : CampusNet
    BSSID                  : aa:bb:cc:dd:ee:02
    Signal                 : 88%
"""

NMCLI = r"""*:CampusNet:AA\:BB\:CC\:DD\:EE\:01:82
 :LabGuest:11\:22\:33\:44\:55\:66:40
 ::DE\:AD\:BE\:EF\:00\:01:15
 :Weird\:Name:00\:11\:22\:33\:44\:55:55
"""


def test_netsh_networks():
    nets = parse_netsh_networks(NETSH)
    assert [(n.ssid, n.bssid) for n in nets] == [
        ("CampusNet", "aa:bb:cc:dd:ee:01"), ("CampusNet", "aa:bb:cc:dd:ee:02"),
        ("<hidden>", "11:22:33:44:55:66")]
    assert nets[0].rssi == -60 and nets[1].rssi == -78 and nets[2].rssi == -90


def test_netsh_connected():
    assert parse_netsh_connected(IFACES) == "aa:bb:cc:dd:ee:02"
    assert parse_netsh_connected(IFACES.replace("connected", "disconnected")) is None


def test_nmcli():
    nets = parse_nmcli(NMCLI)
    assert len(nets) == 4
    assert nets[0].connected and nets[0].bssid == "aa:bb:cc:dd:ee:01" and nets[0].rssi == -59
    assert nets[2].ssid == "<hidden>"
    assert nets[3].ssid == "Weird:Name"


def test_pct_to_dbm_bounds():
    assert pct_to_dbm(0) == -100 and pct_to_dbm(100) == -50
