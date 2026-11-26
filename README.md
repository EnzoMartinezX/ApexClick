# ApexClick v2

Suite multifunción de autoclicker, múltiples zonas holográficas y grabador/editor de macros en Python (`customtkinter` + `pynput`) con estética **Negro Obsidiana y Azul Neón**.

## Secciones

1. **⚡ Autoclicker**
   - Modos **Mantener (Hold)** y **Click** (Simple, Doble o Triple).
   - Control de **Mouse** o **Teclado** (tecla individual o **combinaciones** como `CTRL + W`, `SHIFT + E`).
   - Dos cronómetros digitales independientes (`MIN : SEG . MS`) con botón **⏱ Medir** y opción **Sin soltar (∞)**.
2. **◎ Zonas (2° Mouse)**
   - Miras holográficas en forma de rombo, compactas y **sin fondo** (100% transparentes), con colores personalizables (**Verde, Rojo, Celeste, Rosado, Arcoíris RGB animado** o color a elección) y opción para mostrar u ocultar números.
   - Soporte para **múltiples zonas numeradas** (`#1`, `#2`, `#3`...) con modos **Mouse real**, **Ambos (Real + Zonas)** y **Solo zonas fijas**.
   - Clicks atómicos a nivel kernel (`SendInput`) con soporte multi-monitor y *click-through* automático al iniciar.
3. **🎬 Macros**
   - Grabación selectiva de **Teclado**, **Mouse (Clicks/Scroll)** y **Movimientos** mediante hook Win32 `WH_MOUSE_LL` asíncrono de baja latencia, conservando automáticamente la espera final al detener la grabación como paso `[PAUSA]` editable.
   - Emisión nativa Win32 (`SendInput` con código virtual + *scan code* real y pulso mínimo compatible con juegos a 60 FPS / cursor bloqueado) y pre-posicionamiento automático para la barra de tareas y menús en Windows 11.
   - **Editor visual de pasos** (borrar pasos, insertar pausas manuales y ajustar esperas directamente).
   - **Biblioteca rápida** de macros guardadas en `macros/*.json`, control de velocidad (`0.5x`–`2x`), repeticiones o bucle infinito con pausa entre ciclos y liberación garantizada de teclas/botones al detener.
4. **⏱ Límites y Seguridad**
   - **Parada automática** por cantidad de ciclos o minutos.
   - **Cuenta regresiva** configurable antes de iniciar.
   - **Filtro por ventana activa**: auto-pausa al cambiar de app y reanuda al volver.
   - **Failsafe de esquina** `(0, 0)` y **sonidos de aviso** suaves opcionales.
5. **💾 Perfiles, Atajos e Idioma**
   - Guardado automático en `config.json` y **perfiles/presets** nombrados.
   - Selector de idioma en vivo (**Español** por defecto / **English**) sin reiniciar la app.
   - Atajos globales personalizables (`F6`, `F7`, `F8` por defecto) y **Modo Mini-HUD flotante** con contador de ciclos/macros y transiciones sin parpadeo.

## Instalación y Uso

```bash
pip install -r requirements.txt
python autoclicker.py
```

## Compilar ejecutable (Windows)

Ejecutá `build.bat` para generar `dist/ApexClick.exe`.
