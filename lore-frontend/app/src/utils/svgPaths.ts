/**
 * SVG Path Calculation Utilities
 * Provides helper functions for calculating connection paths between elements
 */

export interface Point {
  x: number;
  y: number;
}

/**
 * Calculate a quadratic bezier curve path between two points
 * The curve will arch upward for a more organic feel
 */
export const calculateConnectionPath = (
  start: Point,
  end: Point
): string => {
  // Control point for the curve - positioned above the midpoint
  const controlX = (start.x + end.x) / 2;
  const controlY = Math.min(start.y, end.y) - 40;

  // M = move to start, Q = quadratic bezier curve
  return `M ${start.x} ${start.y} Q ${controlX} ${controlY} ${end.x} ${end.y}`;
};

/**
 * Calculate position of items arranged in a circle
 * @param index - Index of the item (0-based)
 * @param total - Total number of items
 * @param radius - Radius of the circle
 * @param center - Center point of the circle
 * @returns {Point} - Calculated position
 */
export const calculateCircularPosition = (
  index: number,
  total: number,
  radius: number,
  center: Point
): Point => {
  // Start from top (-90°) and distribute evenly
  const angleInDegrees = (index * 360 / total) - 90;
  const angleInRadians = angleInDegrees * (Math.PI / 180);

  return {
    x: center.x + radius * Math.cos(angleInRadians),
    y: center.y + radius * Math.sin(angleInRadians)
  };
};

/**
 * Calculate the distance between two points
 */
export const calculateDistance = (p1: Point, p2: Point): number => {
  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  return Math.sqrt(dx * dx + dy * dy);
};
