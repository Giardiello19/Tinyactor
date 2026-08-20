# Cara a cara — elegir entre dos audios sin oírlos

Captura lo que suena por el cable y lo dibuja: forma de onda, tramos de habla
y medidas comparadas. **La elección la haces tú**; la herramienta solo pone
delante lo que no puedes oír.

## Cómo se usa

```powershell
cd C:\ruta\a\vozbot
python -m vozbot.cara_a_cara
```

1. Elige el dispositivo de captura (busca `CABLE Output`; suele salir marcado solo)
2. Pulsa **Capturar A** y, en el juego, dale al play de la opción A
3. Pulsa **Capturar B** y dale al play de la opción B
4. Compara y elige en el juego

Los segundos por defecto son 8. Súbelos si los audios son más largos.

## Qué mirar según la indicación

| Indicación | Dónde está la diferencia |
|---|---|
| **Susurrando** | *Voz con vibración* baja (cerca de 0 %) y *Aire* alto. El susurro casi no hace vibrar las cuerdas: es la medida más clara de todas. |
| **Eufórico, Emocionado** | *Tono medio* alto, *Movimiento del tono* grande, *Ritmo* rápido, poco *Silencio* |
| **Sereno, Calmado** | *Tono* grave y estable (±pocos Hz), *Ritmo* lento, mucho *Silencio* |
| **Enojado** | *Volumen* alto y *Rango dinámico* grande: sube y baja de golpe |
| **Triste** | *Volumen* bajo, *Tono* grave, *Ritmo* lento, pausas largas |
| **Sorprendido** | *Movimiento del tono* muy grande, subidas bruscas |
| **Gritando** | *Volumen máximo* alto y *Tono* por encima de lo normal |

## Cómo leer el dibujo

Cada barra vertical es el volumen en ese instante. Las **bandas de color** son
los tramos de habla, numerados. Los huecos entre bandas son las pausas.

Así se ve de un vistazo:

- Muchas bandas juntas y estrechas → habla rápida y entrecortada
- Pocas bandas anchas con huecos grandes → habla pausada
- Barras altas y desiguales → mucha expresión
- Barras bajas y parejas → monótona o susurrada

## Si no capta nada

Comprueba que el dispositivo sea el del cable y que el audio del juego salga
por ahí. La misma prueba que usas para el bot sirve: si `probar_audio.py`
funciona, este también debería.

Y ten en cuenta que la captura empieza al pulsar el botón: dale al play del
juego en ese momento, no antes.
