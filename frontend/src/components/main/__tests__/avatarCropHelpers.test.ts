import { describe, it, expect } from 'vitest'
import {
  AVATAR_OUTPUT,
  centredOffset,
  clampOffset,
  coverScale,
  sourceRect,
  zoomAround,
} from '../avatarCropHelpers'

const VIEW = { width: 150, height: 200 }

describe('AVATAR_OUTPUT', () => {
  it('is a 3:4 portrait', () => {
    expect(AVATAR_OUTPUT.width / AVATAR_OUTPUT.height).toBe(3 / 4)
  })
})

describe('coverScale', () => {
  it('scales the image until it covers both sides of the viewport', () => {
    // A wide image is limited by the viewport's height.
    expect(coverScale(800, 400, VIEW)).toBe(0.5)
    // A tall image is limited by the viewport's width.
    expect(coverScale(300, 1000, VIEW)).toBe(0.5)
    expect(coverScale(100, 50, VIEW)).toBe(4)
  })

  it('degrades to 1 for an image without size', () => {
    expect(coverScale(0, 100, VIEW)).toBe(1)
  })
})

describe('clampOffset', () => {
  it('keeps an offset at which the image covers the viewport', () => {
    expect(clampOffset({ x: -50, y: -10 }, 600, 400, 0.5, VIEW)).toEqual({ x: -50, y: 0 })
  })

  it('pulls a side of the image that lies inside the viewport back to the edge', () => {
    // Shown at 300 × 200: x may range from -150 to 0, y must be 0.
    expect(clampOffset({ x: 20, y: 0 }, 600, 400, 0.5, VIEW)).toEqual({ x: 0, y: 0 })
    expect(clampOffset({ x: -180, y: -5 }, 600, 400, 0.5, VIEW)).toEqual({ x: -150, y: 0 })
  })
})

describe('centredOffset', () => {
  it('centres the overhang on both axes', () => {
    expect(centredOffset(600, 400, 0.5, VIEW)).toEqual({ x: -75, y: 0 })
  })
})

describe('zoomAround', () => {
  it('keeps the image point under the anchor in place', () => {
    const offset = { x: -75, y: 0 }
    const anchor = { x: 75, y: 100 }
    const zoomed = zoomAround(offset, 0.5, 1, anchor, 600, 400, VIEW)
    // The image point under the anchor was (300, 200); at scale 1 it stays under it.
    expect(zoomed).toEqual({ x: -225, y: -100 })
  })

  it('clamps when zooming out would uncover the viewport', () => {
    const zoomed = zoomAround({ x: -400, y: -300 }, 1, 0.5, { x: 0, y: 0 }, 600, 400, VIEW)
    expect(zoomed).toEqual(clampOffset(zoomed, 600, 400, 0.5, VIEW))
    expect(zoomed.y).toBe(0)
  })
})

describe('sourceRect', () => {
  it('maps the viewport back into image pixels, keeping its shape', () => {
    expect(sourceRect({ x: -75, y: 0 }, 0.5, VIEW)).toEqual({ x: 150, y: 0, width: 300, height: 400 })
    expect(sourceRect({ x: -225, y: -100 }, 1, VIEW)).toEqual({ x: 225, y: 100, width: 150, height: 200 })
  })
})
