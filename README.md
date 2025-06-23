# blender-nail

Blender addon to do live UV mapping like Valve's Hammer editor. If you've ever built maps in Hammer, this addon should feel very familiar.

## Installation

1. [Download the repository as a zip](https://github.com/aurycat/blender-nail/archive/refs/heads/main.zip), but don't unzip.
2. In Blender, go to `Edit > Preferences > Add-ons > Dropdown in the top right > Install from Disk` and select the zip file.

## Usage

The following video shows basic usage, plus tips & tricks:

https://github.com/user-attachments/assets/2c822011-d168-47d5-8b12-8fff15938463

## Advanced info about texture alignment

[This article ](https://developer.valvesoftware.com/wiki/Texture_alignment) explains texture alignment in Hammer. The same information is true of Nail, although "World Alignment" is called "Axis Alignment" in Nail. Nail uses the term "World Alignment" and "Object Alignment" to refer to whether alignment is performed relative to the entire scene, or relative to the current Mesh object.

Hammer's Alt+RightClick feature can be done in Nail by using `Nail > Copy Active to Selected`.

Texture-locked movement is the same as enabling "Texture Lock" in Hammer (which is enabled by default).

Like in Hammer, Texture-locked Move and Scale will update the Shift and Scale texture properties, respectively, to keep the texture in the same relative position as the face moves. As a consequence, the texture can become unaligned with other faces.

Also like Hammer, texture-locked Rotate does not affect the Rotation texture property. Instead, an internal UV axis is kept for every face. Performing a texture-locked Rotate adjusts that axis. There is no direct way in Nail (nor Hammer) to manually adjust a face's UV axis, but you can copy the UV axis from one face to others using `Nail > Copy Active to Selected`.

Unlike Hammer, when applying a texture-locked movement to a face with non-zero Rotation property, the texture will appear to "snap" when the movement is complete. Although jarring, this behavior is expected -- the face is adjusting the Shift value to ensure it stays aligned with other faces. Hammer does not do that.

## License

[MIT](https://mit-license.org/)
