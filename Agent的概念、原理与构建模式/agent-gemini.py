import ast
import inspect
import os
import re
from string import Template
from typing import List, Callable, Tuple

import click
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
import platform

from prompt_template import react_system_prompt_template


class ReActAgent:
    def __init__(self, tools: List[Callable], model: str, project_directory: str):
        self.tools = { func.__name__: func for func in tools }
        self.model = model
        self.project_directory = project_directory
        os.environ["GOOGLE_API_KEY"] = self.get_api_key()
        self.client = llm = ChatGoogleGenerativeAI(
            model=self.model,
            temperature=0,
            max_tokens=None,
            timeout=None,
            max_retries=2,
            # other params...
        )

    def run(self, user_input: str):
        messages = [
            ("system", self.render_system_prompt(react_system_prompt_template)),
            ("human", f"<question>{user_input}</question>")
        ]
        while True:

            # 請求模型
            content = self.call_model(messages)
            print(f"\n\n🤖 模型輸出：{content}")
            # 檢測 Thought
            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                thought = thought_match.group(1)
                print(f"\n\n💭 Thought: {thought}")

            # 檢測模型是否輸出 Final Answer，如果是的話，直接返回
            if "<final_answer>" in content:
                final_answer = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                return final_answer.group(1)

            # 檢測 Action
            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                raise RuntimeError("模型未輸出 <action>")
            action = action_match.group(1)
            print(f"\n\n🔧 Action: {action}")
            tool_name, args = self.parse_action(action)

            print(f"\n\n🔧 Action: {tool_name}({', '.join(args)})")
            # 只有終端命令才需要詢問用戶，其他的工具直接執行
            should_continue = input(f"\n\n是否繼續？（Y/N）") if tool_name == "run_terminal_command" else "y"
            if should_continue.lower() != 'y':
                print("\n\n操作已取消。")
                return "操作被用戶取消"

            try:
                observation = self.tools[tool_name](*args)
            except Exception as e:
                observation = f"工具執行錯誤：{str(e)}"
            print(f"\n\n🔍 Observation：{observation}")
            obs_msg = f"<observation>{observation}</observation>"
            messages.append({"role": "user", "content": obs_msg})


    def get_tool_list(self) -> str:
        """生成工具列表字符串，包含函數簽名和簡要說明"""
        tool_descriptions = []
        for func in self.tools.values():
            name = func.__name__
            signature = str(inspect.signature(func))
            doc = inspect.getdoc(func)
            tool_descriptions.append(f"- {name}{signature}: {doc}")
        return "\n".join(tool_descriptions)

    def render_system_prompt(self, system_prompt_template: str) -> str:
        """渲染系統提示模板，替換變量"""
        tool_list = self.get_tool_list()
        file_list = ", ".join(
            os.path.abspath(os.path.join(self.project_directory, f))
            for f in os.listdir(self.project_directory)
        )
        return Template(system_prompt_template).substitute(
            operating_system=self.get_operating_system_name(),
            tool_list=tool_list,
            file_list=file_list
        )

    @staticmethod
    def get_api_key() -> str:
        """Load the API key from an environment variable."""
        load_dotenv()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("未找到 OPENROUTER_API_KEY 環境變量，請在 .env 文件中設置。")
        return api_key

    def call_model(self, messages):
        print("\n\n正在請求模型，請稍等...")

        response = self.client.invoke(messages)
        content = response.content
        messages.append({"role": "assistant", "content": content})
        return content

    def parse_action(self, code_str: str) -> Tuple[str, List[str]]:
        match = re.match(r'(\w+)\((.*)\)', code_str, re.DOTALL)
        if not match:
            raise ValueError("Invalid function call syntax")

        func_name = match.group(1)
        args_str = match.group(2).strip()

        # 手動解析參數，特別處理包含多行內容的字符串
        args = []
        current_arg = ""
        in_string = False
        string_char = None
        i = 0
        paren_depth = 0
        
        while i < len(args_str):
            char = args_str[i]
            
            if not in_string:
                if char in ['"', "'"]:
                    in_string = True
                    string_char = char
                    current_arg += char
                elif char == '(':
                    paren_depth += 1
                    current_arg += char
                elif char == ')':
                    paren_depth -= 1
                    current_arg += char
                elif char == ',' and paren_depth == 0:
                    # 遇到頂層逗號，結束當前參數
                    args.append(self._parse_single_arg(current_arg.strip()))
                    current_arg = ""
                else:
                    current_arg += char
            else:
                current_arg += char
                if char == string_char and (i == 0 or args_str[i-1] != '\\'):
                    in_string = False
                    string_char = None
            
            i += 1
        
        # 添加最後一個參數
        if current_arg.strip():
            args.append(self._parse_single_arg(current_arg.strip()))
        
        return func_name, args
    
    def _parse_single_arg(self, arg_str: str):
        """解析單個參數"""
        arg_str = arg_str.strip()
        
        # 如果是字符串字面量
        if (arg_str.startswith('"') and arg_str.endswith('"')) or \
           (arg_str.startswith("'") and arg_str.endswith("'")):
            # 移除外層引號並處理轉義字符
            inner_str = arg_str[1:-1]
            # 處理常見的轉義字符
            inner_str = inner_str.replace('\\"', '"').replace("\\'", "'")
            inner_str = inner_str.replace('\\n', '\n').replace('\\t', '\t')
            inner_str = inner_str.replace('\\r', '\r').replace('\\\\', '\\')
            return inner_str
        
        # 嘗試使用 ast.literal_eval 解析其他類型
        try:
            return ast.literal_eval(arg_str)
        except (SyntaxError, ValueError):
            # 如果解析失敗，返回原始字符串
            return arg_str

    def get_operating_system_name(self):
        os_map = {
            "Darwin": "macOS",
            "Windows": "Windows",
            "Linux": "Linux"
        }

        return os_map.get(platform.system(), "Unknown")


def read_file(file_path):
    """用於讀取文件內容"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

def write_to_file(file_path, content):
    """將指定內容寫入指定文件"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content.replace("\\n", "\n"))
    return "寫入成功"

def run_terminal_command(command):
    """用於執行終端命令"""
    import subprocess
    run_result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return "執行成功" if run_result.returncode == 0 else run_result.stderr

@click.command()
@click.argument('project_directory',
                type=click.Path(exists=True, file_okay=False, dir_okay=True))
def main(project_directory):
    project_dir = os.path.abspath(project_directory)

    tools = [read_file, write_to_file, run_terminal_command]
    agent = ReActAgent(tools=tools, model="gemini-2.0-flash", project_directory=project_dir)

    task = input("請輸入任務：")

    final_answer = agent.run(task)

    print(f"\n\n✅ Final Answer：{final_answer}")

if __name__ == "__main__":
    main()
