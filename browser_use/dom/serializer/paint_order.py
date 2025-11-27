from collections import defaultdict
from dataclasses import dataclass

from browser_use.dom.views import SimplifiedNode

"""
Helper class for maintaining a union of rectangles (used for order of elements calculation)
"""


@dataclass(frozen=True, slots=True)
class Rect:
	"""Closed axis-aligned rectangle with (x1,y1) bottom-left, (x2,y2) top-right."""

	x1: float
	y1: float
	x2: float
	y2: float

	def __post_init__(self):
		if not (self.x1 <= self.x2 and self.y1 <= self.y2):
			return False

	# --- fast relations ----------------------------------------------------
	def area(self) -> float:
		return (self.x2 - self.x1) * (self.y2 - self.y1)

	def intersects(self, other: 'Rect') -> bool:
		return not (self.x2 <= other.x1 or other.x2 <= self.x1 or self.y2 <= other.y1 or other.y2 <= self.y1)

	def contains(self, other: 'Rect') -> bool:
		return self.x1 <= other.x1 and self.y1 <= other.y1 and self.x2 >= other.x2 and self.y2 >= other.y2


class RectUnionPure:
	"""
	Maintains a *disjoint* set of rectangles.
	No external dependencies - fine for a few thousand rectangles.
	"""

	__slots__ = ('_rects',)

	def __init__(self):
		self._rects: list[Rect] = []

	# -----------------------------------------------------------------
	def _split_diff(self, a: Rect, b: Rect) -> list[Rect]:
		r"""
		Return list of up to 4 rectangles = a \ b.
		Assumes a intersects b.
		"""
		parts = []

		# Bottom slice
		if a.y1 < b.y1:
			parts.append(Rect(a.x1, a.y1, a.x2, b.y1))
		# Top slice
		if b.y2 < a.y2:
			parts.append(Rect(a.x1, b.y2, a.x2, a.y2))

		# Middle (vertical) strip: y overlap is [max(a.y1,b.y1), min(a.y2,b.y2)]
		y_lo = max(a.y1, b.y1)
		y_hi = min(a.y2, b.y2)

		# Left slice
		if a.x1 < b.x1:
			parts.append(Rect(a.x1, y_lo, b.x1, y_hi))
		# Right slice
		if b.x2 < a.x2:
			parts.append(Rect(b.x2, y_lo, a.x2, y_hi))

		return parts

	# -----------------------------------------------------------------
	def contains(self, r: Rect) -> bool:
		"""
		True iff r is fully covered by the current union.
		"""
		if not self._rects:
			return False

		stack = [r]
		for s in self._rects:
			new_stack = []
			for piece in stack:
				if s.contains(piece):
					# piece completely gone
					continue
				if piece.intersects(s):
					new_stack.extend(self._split_diff(piece, s))
				else:
					new_stack.append(piece)
			if not new_stack:  # everything eaten – covered
				return True
			stack = new_stack
		return False  # something survived

	# -----------------------------------------------------------------
	def add(self, r: Rect) -> bool:
		"""
		Insert r unless it is already covered.
		Returns True if the union grew.
		"""
		if self.contains(r):
			return False

		pending = [r]
		i = 0
		while i < len(self._rects):
			s = self._rects[i]
			new_pending = []
			changed = False
			for piece in pending:
				if piece.intersects(s):
					new_pending.extend(self._split_diff(piece, s))
					changed = True
				else:
					new_pending.append(piece)
			pending = new_pending
			if changed:
				# s unchanged; proceed with next existing rectangle
				i += 1
			else:
				i += 1

		# Any left‑over pieces are new, non‑overlapping areas
		self._rects.extend(pending)
		return True


class PaintOrderRemover:
	"""
	Calculates which elements should be removed based on the paint order parameter.
	"""

	def __init__(self, root: SimplifiedNode):
		self.root = root

	def calculate_paint_order(self) -> None:
		all_simplified_nodes_with_paint_order: list[SimplifiedNode] = []

		# Track parent relationships to compute effective (clipped) rects
		parent_map: dict[int, SimplifiedNode | None] = {}

		def collect_paint_order(node: SimplifiedNode, parent: SimplifiedNode | None = None) -> None:
			parent_map[id(node)] = parent
			if (
				node.original_node.snapshot_node
				and node.original_node.snapshot_node.paint_order is not None
				and node.original_node.snapshot_node.bounds is not None
			):
				all_simplified_nodes_with_paint_order.append(node)

			for child in node.children:
				collect_paint_order(child, node)

		collect_paint_order(self.root, None)

		grouped_by_paint_order: defaultdict[int, list[SimplifiedNode]] = defaultdict(list)

		for node in all_simplified_nodes_with_paint_order:
			if node.original_node.snapshot_node and node.original_node.snapshot_node.paint_order is not None:
				grouped_by_paint_order[node.original_node.snapshot_node.paint_order].append(node)

		# Track rectangles we added for coverage checks
		active_rects: list[tuple[Rect, SimplifiedNode]] = []

		def _rect_from_node(n: SimplifiedNode) -> Rect | None:
			if not n.original_node.snapshot_node or not n.original_node.snapshot_node.bounds:
				return None
			b = n.original_node.snapshot_node.bounds
			x1 = b.x
			y1 = b.y
			x2 = b.x + b.width
			y2 = b.y + b.height
			if x2 < x1 or y2 < y1:
				return None
			return Rect(x1=x1, y1=y1, x2=x2, y2=y2)

		def _intersect(a: Rect, b: Rect) -> Rect | None:
			x1 = max(a.x1, b.x1)
			y1 = max(a.y1, b.y1)
			x2 = min(a.x2, b.x2)
			y2 = min(a.y2, b.y2)
			if x2 < x1 or y2 < y1:
				return None
			return Rect(x1=x1, y1=y1, x2=x2, y2=y2)

		def _position_value(n: SimplifiedNode | None) -> str:
			if not n or not n.original_node or not n.original_node.snapshot_node:
				return ''
			styles = n.original_node.snapshot_node.computed_styles or {}
			return styles.get('position', '').lower()

		def _is_abs_or_fixed(n: SimplifiedNode | None) -> bool:
			return _position_value(n) in ('fixed', 'absolute')

		def _is_clipping_ancestor(n: SimplifiedNode | None) -> bool:
			"""
			Return True if this ancestor should clip its children based on overflow properties.
			We only treat it as clipping when overflow / overflow-x / overflow-y are explicitly
			set to a value other than 'visible'.
			"""
			if not n or not n.original_node or not n.original_node.snapshot_node:
				return False

			styles = n.original_node.snapshot_node.computed_styles or {}

			def _is_non_visible(val: str | None) -> bool:
				if not val:
					return False
				v = val.lower()
				return v != 'visible'

			return (
				_is_non_visible(styles.get('overflow'))
				or _is_non_visible(styles.get('overflow-x'))
				or _is_non_visible(styles.get('overflow-y'))
			)

		def _effective_rect(node: SimplifiedNode) -> Rect | None:
			"""Clamp node's rect by all ancestor rects that have bounds (approximates clipping)."""
			base = _rect_from_node(node)
			if base is None:
				return None
			# Absolutely/fixed positioned elements should not be clipped by ancestors
			if _is_abs_or_fixed(node):
				return base

			current_parent = parent_map.get(id(node))
			while current_parent is not None:
				parent_rect = _rect_from_node(current_parent)
				# Only intersect when this ancestor actually clips its children (overflow != visible)
				if parent_rect is not None and _is_clipping_ancestor(current_parent):
					base = _intersect(base, parent_rect)
					if base is None:
						return None
				# Stop climbing once we encounter an ancestor that should not be clipped further
				if _is_abs_or_fixed(current_parent):
					break
				current_parent = parent_map.get(id(current_parent))

			return base

		def _is_descendant(node: SimplifiedNode, ancestor: SimplifiedNode) -> bool:
			"""Return True if `node` is a (direct or indirect) descendant of `ancestor`."""
			current_parent = parent_map.get(id(node))
			while current_parent is not None:
				if current_parent is ancestor:
					return True
				current_parent = parent_map.get(id(current_parent))
			return False

		def _is_covered_by_non_descendants(target_rect: Rect, target_node: SimplifiedNode) -> bool:
			"""
			Check if `target_rect` is fully covered by the union of previously
			painted rects that do NOT belong to descendants of `target_node`.
			"""
			if not active_rects:
				return False

			union = RectUnionPure()
			for existing_rect, existing_node in active_rects:
				if _is_descendant(existing_node, target_node):
					# Skip rectangles from our own descendants
					continue
				union.add(existing_rect)

			return union.contains(target_rect)

		for paint_order, nodes in sorted(grouped_by_paint_order.items(), key=lambda x: -x[0]):
			rects_to_add: list[tuple[Rect, SimplifiedNode]] = []

			for node in nodes:
				rect = _effective_rect(node)
				if rect is None or rect.area() == 0:
					node.ignored_by_paint_order = True
					continue  # no effective painted area

				if _is_covered_by_non_descendants(rect, node):
					node.ignored_by_paint_order = True

				# don't add to the nodes if opacity is less then 0.95 or background-color is transparent
				if (
					node.original_node.snapshot_node.computed_styles
					and node.original_node.snapshot_node.computed_styles.get('background-color', 'rgba(0, 0, 0, 0)')
					== 'rgba(0, 0, 0, 0)'
				) or (
					node.original_node.snapshot_node.computed_styles
					and float(node.original_node.snapshot_node.computed_styles.get('opacity', '1'))
					< 0.8  # this is highly vibes based number
				):
					continue

				rects_to_add.append((rect, node))

			for rect, n in rects_to_add:
				active_rects.append((rect, n))

		return None
