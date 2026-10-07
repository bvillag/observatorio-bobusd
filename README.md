# Observatorio BOB/USD

Página pública que mide el precio del dólar en Bolivia y las diferencias (spreads) entre mercados: el P2P de stablecoins y las compras bancarias que forman el tipo de cambio oficial (TCO) del BCB.

**Página:** https://bvillag.github.io/observatorio-bobusd/

## Cómo funciona

1. Un recolector en un servidor (VPS) guarda cada minuto el libro completo de anuncios BOB/USDT de Binance P2P en SQLite.
2. Cada 10 minutos, `scripts/export.py` lee esa base y los archivos del BCB ([acamperob/bcb-tco](https://github.com/acamperob/bcb-tco)), calcula las estadísticas y escribe `data/obs.json` y CSV diarios.
3. `scripts/publish.sh` sube `data/` a este repositorio y GitHub Pages publica la página.

## Estadísticas

- **Base P2P − TCO** (`mid P2P / TCO vigente − 1`), su distribución y su vida media con un AR(1).
- **Banda de no arbitraje** con una autorregresión con umbral (Balke y Fomby, 1997).
- **Costo según el monto**: precio efectivo para 100 a 50.000 USDT contra el libro, respetando los límites de cada anuncio; descuento bancario por tamaño de operación.
- **Liquidez**: profundidad por lado, límites de los anuncios, spread por hora y día.
- **Dinámica**: volatilidad realizada, autocorrelación y razón de varianzas de Lo y MacKinlay (1988).
- **Bancos**: concentración (HHI), dispersión de precios dentro del día.

## Datos

`data/obs.json` (todo lo que dibuja la página), `data/csv/tco.csv` y `data/csv/p2p_AAAA-MM-DD.csv` (precios P2P por minuto). Horas en UTC.

## Autor

Bruno Villagomez Caro. Proyecto de investigación sobre la formación del tipo de cambio en Bolivia.
