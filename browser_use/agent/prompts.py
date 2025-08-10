import importlib.resources
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Optional

from browser_use.llm.messages import ContentPartImageParam, ContentPartTextParam, ImageURL, SystemMessage, UserMessage
from browser_use.observability import observe_debug
from browser_use.utils import is_new_tab_page

if TYPE_CHECKING:
	from browser_use.agent.views import AgentStepInfo
	from browser_use.browser.views import BrowserStateSummary
	from browser_use.filesystem.file_system import FileSystem


class SystemPrompt:
	def __init__(
		self,
		action_description: str,
		max_actions_per_step: int = 10,
		override_system_message: str | None = None,
		extend_system_message: str | None = None,
		use_thinking: bool = True,
		flash_mode: bool = False,
	):
		self.default_action_description = action_description
		self.max_actions_per_step = max_actions_per_step
		self.use_thinking = use_thinking
		self.flash_mode = flash_mode
		prompt = ''
		if override_system_message:
			prompt = override_system_message
		else:
			self._load_prompt_template()
			prompt = self.prompt_template.format(max_actions=self.max_actions_per_step)

		if extend_system_message:
			prompt += f'\n{extend_system_message}'

		self.system_message = SystemMessage(content=prompt, cache=True)

	def _load_prompt_template(self) -> None:
		"""Load the prompt template from the markdown file."""
		try:
			# Choose the appropriate template based on flash_mode and use_thinking settings
			if self.flash_mode:
				template_filename = 'system_prompt_flash.md'
			elif self.use_thinking:
				template_filename = 'system_prompt.md'
			else:
				template_filename = 'system_prompt_no_thinking.md'

			# This works both in development and when installed as a package
			with importlib.resources.files('browser_use.agent').joinpath(template_filename).open('r', encoding='utf-8') as f:
				self.prompt_template = f.read()
		except Exception as e:
			raise RuntimeError(f'Failed to load system prompt template: {e}')

	def get_system_message(self) -> SystemMessage:
		"""
		Get the system prompt for the agent.

		Returns:
		    SystemMessage: Formatted system prompt
		"""
		return self.system_message


# Functions:
# {self.default_action_description}

# Example:
# {self.example_response()}
# Your AVAILABLE ACTIONS:
# {self.default_action_description}


class AgentMessagePrompt:
	vision_detail_level: Literal['auto', 'low', 'high']

	def __init__(
		self,
		browser_state_summary: 'BrowserStateSummary',
		file_system: 'FileSystem',
		agent_history_description: str | None = None,
		read_state_description: str | None = None,
		task: str | None = None,
		include_attributes: list[str] | None = None,
		step_info: Optional['AgentStepInfo'] = None,
		page_filtered_actions: str | None = None,
		max_clickable_elements_length: int = 40000,
		sensitive_data: str | None = None,
		available_file_paths: list[str] | None = None,
		screenshots: list[str] | None = None,
		vision_detail_level: Literal['auto', 'low', 'high'] = 'auto',
	):
		self.browser_state: 'BrowserStateSummary' = browser_state_summary
		self.file_system: 'FileSystem | None' = file_system
		self.agent_history_description: str | None = agent_history_description
		self.read_state_description: str | None = read_state_description
		self.task: str | None = task
		self.include_attributes = include_attributes
		self.step_info = step_info
		self.page_filtered_actions: str | None = page_filtered_actions
		self.max_clickable_elements_length: int = max_clickable_elements_length
		self.sensitive_data: str | None = sensitive_data
		self.available_file_paths: list[str] | None = available_file_paths
		self.screenshots = screenshots or []
		self.vision_detail_level = vision_detail_level
		assert self.browser_state

	@observe_debug(ignore_input=True, ignore_output=True, name='_get_browser_state_description')
	def _get_browser_state_description(self) -> str:
		# Get clickable and hoverable elements separately
		clickable_text = self.browser_state.element_tree.clickable_elements_to_string(include_attributes=self.include_attributes)
		hoverable_text = self.browser_state.element_tree.hoverable_elements_to_string(include_attributes=self.include_attributes)

		# Handle truncation for the combined content
		combined_length = len(clickable_text) + len(hoverable_text)
		if combined_length > self.max_clickable_elements_length:
			# Prioritize clickable elements, then add hoverable elements if space allows
			if len(clickable_text) > self.max_clickable_elements_length:
				clickable_text = clickable_text[: self.max_clickable_elements_length]
				hoverable_text = ''  # No space for hoverable elements
			else:
				remaining_space = self.max_clickable_elements_length - len(clickable_text)
				hoverable_text = hoverable_text[: remaining_space]
			truncated_text = f' (truncated to {self.max_clickable_elements_length} characters)'
		else:
			truncated_text = ''

		has_content_above = (self.browser_state.pixels_above or 0) > 0
		has_content_below = (self.browser_state.pixels_below or 0) > 0

		# Enhanced page information for the model
		page_info_text = ''
		if self.browser_state.page_info:
			pi = self.browser_state.page_info
			# Compute page statistics dynamically
			pages_above = pi.pixels_above / pi.viewport_height if pi.viewport_height > 0 else 0
			pages_below = pi.pixels_below / pi.viewport_height if pi.viewport_height > 0 else 0
			total_pages = pi.page_height / pi.viewport_height if pi.viewport_height > 0 else 0
			current_page_position = pi.scroll_y / max(pi.page_height - pi.viewport_height, 1)
			page_info_text = f'Page info: {pi.viewport_width}x{pi.viewport_height}px viewport, {pi.page_width}x{pi.page_height}px total page size, {pages_above:.1f} pages above, {pages_below:.1f} pages below, {total_pages:.1f} total pages, at {current_page_position:.0%} of page'

		# Format clickable elements section
		clickable_section = ''
		if clickable_text != '':
			if has_content_above:
				if self.browser_state.page_info:
					pi = self.browser_state.page_info
					pages_above = pi.pixels_above / pi.viewport_height if pi.viewport_height > 0 else 0
					clickable_section = f'... {self.browser_state.pixels_above} pixels above ({pages_above:.1f} pages) - scroll to see more or extract structured data if you are looking for specific information ...\n{clickable_text}'
				else:
					clickable_section = f'... {self.browser_state.pixels_above} pixels above - scroll to see more or extract structured data if you are looking for specific information ...\n{clickable_text}'
			else:
				clickable_section = f'[Start of page]\n{clickable_text}'
			if has_content_below:
				if self.browser_state.page_info:
					pi = self.browser_state.page_info
					pages_below = pi.pixels_below / pi.viewport_height if pi.viewport_height > 0 else 0
					clickable_section = f'{clickable_section}\n... {self.browser_state.pixels_below} pixels below ({pages_below:.1f} pages) - scroll to see more or extract structured data if you are looking for specific information ...'
				else:
					clickable_section = f'{clickable_section}\n... {self.browser_state.pixels_below} pixels below - scroll to see more or extract structured data if you are looking for specific information ...'
			else:
				clickable_section = f'{clickable_section}\n[End of page]'
		else:
			clickable_section = 'no clickable elements'

		# Format hoverable elements section
		hoverable_section = ''
		if hoverable_text != '':
			hoverable_section = hoverable_text
		else:
			hoverable_section = 'no hoverable elements'

		tabs_text = ''
		current_tab_candidates = []

		# Find tabs that match both URL and title to identify current tab more reliably
		for tab in self.browser_state.tabs:
			if tab.url == self.browser_state.url and tab.title == self.browser_state.title:
				current_tab_candidates.append(tab.page_id)

		# If we have exactly one match, mark it as current
		# Otherwise, don't mark any tab as current to avoid confusion
		current_tab_id = current_tab_candidates[0] if len(current_tab_candidates) == 1 else None

		for tab in self.browser_state.tabs:
			tabs_text += f'Tab {tab.page_id}: {tab.url} - {tab.title[:30]}\n'

		current_tab_text = f'Current tab: {current_tab_id}' if current_tab_id is not None else ''

		# Check if current page is a PDF viewer and add appropriate message
		pdf_message = ''
		if self.browser_state.is_pdf_viewer:
			pdf_message = 'PDF viewer cannot be rendered. In this page, DO NOT use the extract_structured_data action as PDF content cannot be rendered. Use the read_file action on the downloaded PDF in available_file_paths to read the full content.\n\n'

		browser_state = f"""{current_tab_text}
Available tabs:
{tabs_text}
{page_info_text}
{pdf_message}Clickable elements (marked with [index] - can be clicked by agents){truncated_text}:
{clickable_section}

Hoverable elements (marked with [index] or (H) - may reveal additional content when hovered):
{hoverable_section}
"""
		return browser_state

	def _get_agent_state_description(self) -> str:
		if self.step_info:
			step_info_description = f'Step {self.step_info.step_number + 1} of {self.step_info.max_steps} max possible steps\n'
		else:
			step_info_description = ''
		time_str = datetime.now().strftime('%Y-%m-%d %H:%M')
		step_info_description += f'Current date and time: {time_str}'

		_todo_contents = self.file_system.get_todo_contents() if self.file_system else ''
		if not len(_todo_contents):
			_todo_contents = '[Current todo.md is empty, fill it with your plan when applicable]'

		agent_state = f"""
<user_request>
{self.task}
</user_request>
<file_system>
{self.file_system.describe() if self.file_system else 'No file system available'}
</file_system>
<todo_contents>
{_todo_contents}
</todo_contents>
"""
		if self.sensitive_data:
			agent_state += f'<sensitive_data>\n{self.sensitive_data}\n</sensitive_data>\n'

		agent_state += f'<step_info>\n{step_info_description}\n</step_info>\n'
		if self.available_file_paths:
			agent_state += '<available_file_paths>\n' + '\n'.join(self.available_file_paths) + '\n</available_file_paths>\n'
		return agent_state

	@observe_debug(ignore_input=True, ignore_output=True, name='get_user_message')
	def get_user_message(self, use_vision: bool = True) -> UserMessage:
		"""Get complete state as a single cached message"""
		# Don't pass screenshot to model if page is a new tab page, step is 0, and there's only one tab
		if (
			is_new_tab_page(self.browser_state.url)
			and self.step_info is not None
			and self.step_info.step_number == 0
			and len(self.browser_state.tabs) == 1
		):
			use_vision = False

		# Build complete state description
		state_description = (
			'<agent_history>\n'
			+ (self.agent_history_description.strip('\n') if self.agent_history_description else '')
			+ '\n</agent_history>\n'
		)
		state_description += '<agent_state>\n' + self._get_agent_state_description().strip('\n') + '\n</agent_state>\n'
		state_description += '<browser_state>\n' + self._get_browser_state_description().strip('\n') + '\n</browser_state>\n'
		# Only add read_state if it has content
		read_state_description = self.read_state_description.strip('\n').strip() if self.read_state_description else ''
		if read_state_description:
			state_description += '<read_state>\n' + read_state_description + '\n</read_state>\n'

		if self.page_filtered_actions:
			state_description += '<page_specific_actions>\n'
			state_description += self.page_filtered_actions + '\n'
			state_description += '</page_specific_actions>\n'

		if use_vision is True and self.screenshots:
			# Start with text description
			content_parts: list[ContentPartTextParam | ContentPartImageParam] = [ContentPartTextParam(text=state_description)]

			# Add screenshots with labels
			for i, screenshot in enumerate(self.screenshots):
				if i == len(self.screenshots) - 1:
					label = 'Current screenshot:'
				else:
					# Use simple, accurate labeling since we don't have actual step timing info
					label = 'Previous screenshot:'

				# Add label as text content
				content_parts.append(ContentPartTextParam(text=label))

				# Add the screenshot
				content_parts.append(
					ContentPartImageParam(
						image_url=ImageURL(
							url=f'data:image/png;base64,{screenshot}',
							media_type='image/png',
							detail=self.vision_detail_level,
						),
					)
				)

			return UserMessage(content=content_parts, cache=True)

		return UserMessage(content=state_description, cache=True)
