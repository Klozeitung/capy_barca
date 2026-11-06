/**
 * Geometry of the profile-picture crop.
 *
 * The crop dialog shows the image behind a square viewport of ``view`` pixels
 * with a round mask. The image is drawn at ``scale`` (screen pixels per image
 * pixel) with its top-left corner at ``offset`` relative to the viewport. It
 * always covers the whole viewport, so the result never has empty corners.
 * These functions keep that invariant and translate the screen state into the
 * source rectangle that is drawn onto the square output canvas.
 */

export interface Point {
  x: number
  y: number
}

export interface SourceRect {
  x: number
  y: number
  size: number
}

/** Edge length of the uploaded square, in pixels. */
export const AVATAR_OUTPUT_SIZE = 512

/** Largest zoom relative to the scale that just covers the viewport. */
export const AVATAR_MAX_ZOOM = 5

/** The smallest scale at which an image of *width* × *height* covers the viewport. */
export function coverScale(width: number, height: number, view: number): number {
  if (width <= 0 || height <= 0 || view <= 0) return 1
  return Math.max(view / width, view / height)
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
  view: number,
): Point {
  const shownWidth = width * scale
  const shownHeight = height * scale
  // Adding 0 turns a negative zero into a plain zero.
  const clamp = (value: number, shown: number): number =>
    Math.min(0, Math.max(view - shown, value)) + 0
  return { x: clamp(offset.x, shownWidth), y: clamp(offset.y, shownHeight) }
}

/** The offset that centres the image in the viewport at *scale*. */
export function centredOffset(width: number, height: number, scale: number, view: number): Point {
  return { x: (view - width * scale) / 2, y: (view - height * scale) / 2 }
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
  view: number,
): Point {
  const imageX = (anchor.x - offset.x) / oldScale
  const imageY = (anchor.y - offset.y) / oldScale
  const moved = { x: anchor.x - imageX * newScale, y: anchor.y - imageY * newScale }
  return clampOffset(moved, width, height, newScale, view)
}

/** The square of the source image that the viewport shows, in image pixels. */
export function sourceRect(offset: Point, scale: number, view: number): SourceRect {
  return { x: 0 - offset.x / scale, y: 0 - offset.y / scale, size: view / scale }
}
