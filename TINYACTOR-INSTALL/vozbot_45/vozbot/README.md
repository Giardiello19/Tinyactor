# vozbot

Automatiza el juego de lectura: lee el guion y la emoción de la pantalla, se
los pasa a tu app de voz, y entrega el audio por un micrófono virtual
sincronizado con el botón de grabar.

## Instalar

Doble clic en **INSTALAR.bat**. Comprueba Python, instala las librerías,
descarga el navegador y crea el acceso directo.

Aparte hay que instalar **VB-CABLE** (vb-audio.com/Cable) como administrador,
y reiniciar. Windows no permite instalar drivers de audio de otra forma.

## Jugar

```
1. Tu app de voz:   python app.py        (en su carpeta)
2. El panel:        acceso «vozbot» del escritorio
3. Pestaña Web  →   botón «Abrir navegador»
4. En Chrome    →   entra al juego y elige CABLE Output como micrófono
5. Guardar configuración → Probar micrófono virtual → Empezar a jugar
```

## El panel

**Pestaña Web** — textos de los botones, vista del dispositivo (escritorio o
móvil, vertical u horizontal) y el botón para abrir Chrome ya preparado.

**Pestaña Voz y micrófono** — tu app de voz, la voz de Piper a usar, el
dispositivo del cable, el volumen y la velocidad de reproducción.

**Pestaña Razonamiento** — opcional, solo si el juego dejara de exponer el
texto en el DOM.

## Ajustes que quizá quieras tocar

| Situación | Qué cambiar |
|---|---|
| El juego rechaza tomas por calidad | Sube el volumen a +6 o +9 dB |
| Los diálogos van muy lentos | Sube la velocidad a 1.2x o 1.3x |
| Corta el principio de la frase | Pon 0.5 en «espera si no hay cuenta» |
| El juego se ve en escritorio y lo quieres móvil | Elige un perfil de dispositivo |

## Herramientas de diagnóstico

```powershell
python diagnostico.py        # qué lee el bot en la pantalla actual
python probar_audio.py       # si el cable de Windows transporta el audio
python probar_microfono.py   # si el audio llega al navegador
```

Y `python -m vozbot.cara_a_cara` para comparar dos audios visualmente
(ver GUIA-CARA-A-CARA.md).

## Si algo falla

El log del panel dice en qué paso está y por qué vía pulsó cada botón. Cuando
no encuentra nada, enumera lo que ve en pantalla — suele bastar para saber qué
ajustar.

## Una nota

Esto entrega voz sintética a un micrófono. Úsalo en tus propias aplicaciones o
donde tengas permiso: en una plataforma que evalúa la lectura de una persona,
sustituir la voz falsea el resultado y suele ir contra sus términos de uso.
