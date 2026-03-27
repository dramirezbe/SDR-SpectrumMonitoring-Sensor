import uhd

# Conectar a la USRP (detecta la B200 mini conectada por USB)
usrp = uhd.usrp.MultiUSRP()

# Obtener y mostrar información básica del hardware
print("=== USRP Detectada ===")
print("Placa:", usrp.get_mboard_name())
print("Frecuencia de reloj maestro:", usrp.get_master_clock_rate(), "Hz")
print("Rango de ganancias RX:", usrp.get_rx_gain_range())