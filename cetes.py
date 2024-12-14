# INTERES COMPUESTO CETES
def interes_compuesto(inicial,tasa,tiempo):
 monto = inicial*(1+ tasa)**tiempo
 return monto 
i = 100 # monto inicial
t = 0.10 # Tasa de interes
tp = 3 # TIEMPO EN añoss

monto_final = interes_compuesto(i,t,tp)
print(f"El monto final despues de {tp} años sera: ${monto_final:.2f}")

