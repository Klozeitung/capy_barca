/**
 * Geometry of the profile-picture crop.
 *
 * Profile pictures are 3:4 portrait rectangles. The crop dialog shows the
 * image behind a viewport of that shape, ``view`` pixels wide and high. The
 * image is drawn at ``scale`` (screen pixels per image pixel) with its
 * top-left corner at ``offset`` relative to the viewport. It always covers
 * the whole viewport, so the result never has empty edges. These functions
 * keep that invariant and translate the screen state into the source
 * rectangle that is drawn onto the output canvas.
 */

export interface Point {
  x: number
  y: number
}

export interface Size {
  width: number
  height: number
}

export interface SourceRect {
  x: number
  y: number
  width: number
  height: number
}

/** Size of the uploaded picture, in pixels (3:4). */
export const AVATAR_OUTPUT: Size = { width: 480, height: 640 }

/** Largest zoom relative to the scale that just covers the viewport. */
export const AVATAR_MAX_ZOOM = 5

/** The smallest scale at which an image of *width* × *height* covers the viewport. */
export function coverScale(width: number, height: number, view: Size): number {
  if (width <= 0 || height <= 0 || view.width <= 0 || view.height <= 0) return 1
  return Math.max(view.width / width, view.height / height)
}

/**
 * *offset* moved as little as necessary for the image to cover the viewport
 * at *scale*: no side of the image may lie inside the viewport.
 */
export function clampOffset(
  offset: Point,
  width: number,
  height: number,
  scale: number,
  view: Size,
): Point {
  // Adding 0 turns a negative zero into a plain zero.
  const clamp = (value: number, shown: number, visible: number): number =>
    Math.min(0, Math.max(visible - shown, value)) + 0
  return {
    x: clamp(offset.x, width * scale, view.width),
    y: clamp(offset.y, height * scale, view.height),
  }
}

/** The offset that centres the image in the viewport at *scale*. */
export function centredOffset(width: number, height: number, scale: number, view: Size): Point {
  return { x: (view.width - width * scale) / 2, y: (view.height - height * scale) / 2 }
}

/**
 * The offset after zooming from *oldScale* to *newScale*, keeping the image
 * point under *anchor* (a viewport position) in place, then clamped.
 */
export function zoomAround(
  offset: Point,
  oldScale: number,
  newScale: number,
  anchor: Point,
  width: number,
  height: number,
  view: Size,
): Point {
  const imageX = (anchor.x - offset.x) / oldScale
  const imageY = (anchor.y - offset.y) / oldScale
  const moved = { x: anchor.x - imageX * newScale, y: anchor.y - imageY * newScale }
  return clampOffset(moved, width, height, newScale, view)
}

/** The rectangle of the source image that the viewport shows, in image pixels. */
export function sourceRect(offset: Point, scale: number, view: Size): SourceRect {
  return {
    x: 0 - offset.x / scale,
    y: 0 - offset.y / scale,
    width: view.width / scale,
    height: view.height / scale,
  }
}
