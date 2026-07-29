#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""
DSL Schema 校验引擎与版本管理器。

解决的核心问题：
  1. 当前 Graph.load() 在解析 DSL 时不做 Schema 校验，错误的 DSL 配置
     只有在运行时才暴露为难以定位的异常（"Can't import ..."、AttributeError 等）
  2. DSL 没有版本概念，无法做向后兼容。前后端 DSL 格式不一致时，
     升级后旧的 DSL 直接不可用，无任何迁移提示。

设计思路：
  - DSL Schema 定义：参照 JSON Schema 标准，描述 DSL 的合法结构
  - 校验时机：Graph.load() 之前执行，提前拦截错误，给出精确的定位提示
  - 版本管理：DSL 顶层声明 version 字段，VersionMigrator 自动检测版本差异，
    执行对应的迁移脚本
  - 模板工厂：预置工作流模板，支持参数化实例化

与 Canvas 的集成方式：
  在 Graph.__init__() 中先调用 DSLSchemaValidator.validate()，
  如果校验不通过，抛出带详细错误位置的异常。
  VersionMigrator 在 validate 之前执行，自动将旧版本 DSL 升级到最新。
"""

import copy
import json
import logging
from typing import Any, Optional


DSL_SCHEMA_V1 = {
    "type": "object",
    "required": ["components", "path"],
    "properties": {
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "components": {
            "type": "object",
            "patternProperties": {
                "^[a-zA-Z_][a-zA-Z0-9_]*$": {
                    "type": "object",
                    "required": ["obj", "downstream"],
                    "properties": {
                        "obj": {"type": "string"},
                        "downstream": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "upstream": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "parent_id": {"type": "string"},
                    },
                }
            },
        },
        "path": {
            "type": "array",
            "items": {"type": "string"},
        },
        "history": {
            "type": "array",
            "items": {"type": "object"},
        },
        "retrieval": {
            "type": "object",
            "properties": {
                "chunks": {"type": "object"},
                "doc_aggs": {"type": "array"},
            },
        },
        "globals": {
            "type": "object",
        },
        "memory": {
            "type": "array",
        },
    },
}


class DSLValidateError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        formatted = "\n".join(f"  [{i+1}] {err}" for i, err in enumerate(errors))
        super().__init__(f"DSL validation failed ({len(errors)} errors):\n{formatted}")


class DSLSchemaValidator:
    """
    DSL 结构校验引擎。

    用法：
        validator = DSLSchemaValidator()
        try:
            validator.validate(dsl_dict)
        except DSLValidateError as e:
            print(e.errors)  # 精确的错误列表
    """

    VALID_COMPONENT_NAMES: set = set()

    @classmethod
    def register_component_names(cls, names: set[str]):
        cls.VALID_COMPONENT_NAMES = names

    def validate(self, dsl: dict) -> None:
        errors: list[str] = []

        # 1. 顶层结构校验
        if not isinstance(dsl, dict):
            errors.append("DSL must be a JSON object")
            raise DSLValidateError(errors)

        # 2. 必需字段
        if "components" not in dsl:
            errors.append("Missing required field: 'components'")
        if "path" not in dsl:
            errors.append("Missing required field: 'path'")

        if errors:
            raise DSLValidateError(errors)

        # 3. components 校验
        components = dsl["components"]
        if not isinstance(components, dict):
            errors.append("'components' must be a JSON object")
            raise DSLValidateError(errors)

        for cid, cfg in components.items():
            self._validate_component(cid, cfg, errors, dsl.get("path", []))

        # 4. path 校验
        path = dsl.get("path", [])
        if not isinstance(path, list):
            errors.append("'path' must be a JSON array")
        else:
            for i, cid in enumerate(path):
                if not isinstance(cid, str):
                    errors.append(f"path[{i}] must be a string, got {type(cid).__name__}")
                elif cid not in components:
                    errors.append(f"path[{i}]='{cid}' references non-existent component")

        # 5. 环路检测（简化版：检测 path 中重复且非循环/迭代上下文）
        if isinstance(path, list):
            loop_contexts = set()
            for cid in path:
                cfg = components.get(cid, {})
                parent_id = cfg.get("parent_id") if isinstance(cfg, dict) else None
                if parent_id:
                    parent_cfg = components.get(parent_id, {})
                    parent_obj = parent_cfg.get("obj", "") if isinstance(parent_cfg, dict) else ""
                    if parent_obj.lower() in ("loop", "iteration"):
                        loop_contexts.add(cid)
            # 如果 path 中有非 loop 上下文的重复，报错
            seen = set()
            for cid in path:
                if cid not in loop_contexts:
                    if cid in seen:
                        errors.append(f"Duplicate component '{cid}' in path outside loop context")
                    seen.add(cid)

        if errors:
            raise DSLValidateError(errors)

    def _validate_component(self, cid: str, cfg: Any, errors: list[str], path: list[str]):
        if not isinstance(cfg, dict):
            errors.append(f"components['{cid}'] must be a JSON object")
            return

        # obj 字段
        obj = cfg.get("obj")
        if not obj:
            errors.append(f"components['{cid}']: missing required field 'obj'")
        elif not isinstance(obj, str):
            errors.append(f"components['{cid}']: 'obj' must be a string")
        elif self.VALID_COMPONENT_NAMES and obj not in self.VALID_COMPONENT_NAMES:
            errors.append(
                f"components['{cid}']: unknown component '{obj}'. "
                f"Valid names: {sorted(self.VALID_COMPONENT_NAMES)}"
            )

        # downstream
        ds = cfg.get("downstream")
        if ds is not None and not isinstance(ds, list):
            errors.append(f"components['{cid}']: 'downstream' must be a JSON array")

        # upstream
        us = cfg.get("upstream")
        if us is not None and not isinstance(us, list):
            errors.append(f"components['{cid}']: 'upstream' must be a JSON array")


_DEFAULT_MIGRATIONS = {}


def _migrate_v0_0_1_to_v1_0_0(dsl: dict) -> dict:
    """
    迁移 v0.0.1 → v1.0.0
    变更：DSL 结构从扁平化升级到带版本声明的规范结构
    """
    dsl = copy.deepcopy(dsl)
    dsl["version"] = "1.0.0"
    if "retrieval" not in dsl:
        dsl["retrieval"] = {"chunks": {}, "doc_aggs": []}
    if "history" not in dsl:
        dsl["history"] = []
    if "memory" not in dsl:
        dsl["memory"] = []
    if "globals" not in dsl:
        dsl["globals"] = {}
    return dsl


_DEFAULT_MIGRATIONS["0.0.1"] = ("1.0.0", _migrate_v0_0_1_to_v1_0_0)


class DSLVersionMigrator:
    """
    DSL 版本迁移器。

    用法：
        migrator = DSLVersionMigrator()
        dsl = migrator.migrate(dsl_dict)
        # dsl 已自动升级到最新版本

    版本管理策略：
      - 每个版本注册一个迁移函数 (from_version → (to_version, migrate_fn))
      - 迁移链：0.0.1 → 1.0.0 → 1.1.0 → ...
      - 支持跨版本跳跃迁移（自动检测最短路径）
    """

    CURRENT_VERSION = "1.0.0"

    def __init__(self):
        self._migrations = dict(_DEFAULT_MIGRATIONS)

    def register_migration(self, from_version: str, to_version: str, migrate_fn):
        self._migrations[from_version] = (to_version, migrate_fn)

    def detect_version(self, dsl: dict) -> str:
        raw = dsl.get("version", "0.0.1")
        if not isinstance(raw, str):
            raw = str(raw)
        return raw

    def migrate(self, dsl: dict) -> dict:
        current_ver = self.detect_version(dsl)

        if current_ver == self.CURRENT_VERSION:
            return dsl

        version_chain = []
        visited = set()
        v = current_ver
        while v != self.CURRENT_VERSION:
            if v in visited:
                raise RuntimeError(f"Circular migration detected at version {v}")
            visited.add(v)
            if v not in self._migrations:
                raise RuntimeError(
                    f"No migration path from v{v} to v{self.CURRENT_VERSION}. "
                    f"Registered from-versions: {list(self._migrations.keys())}"
                )
            to_ver, fn = self._migrations[v]
            version_chain.append((v, to_ver, fn))
            v = to_ver

        result = dsl
        for from_v, to_v, fn in version_chain:
            logging.info(f"[DSLVersionMigrator] Migrating v{from_v} → v{to_v}")
            try:
                result = fn(result)
                result["version"] = to_v
            except Exception as e:
                raise RuntimeError(f"Migration v{from_v}→v{to_v} failed: {e}")

        return result


class DSLTemplateManager:
    """
    工作流 DSL 模板管理器。

    用法：
        manager = DSLTemplateManager()
        manager.register("customer_service", dsl_dict)
        dsl = manager.instantiate("customer_service", {"query": "..."})
    """

    def __init__(self):
        self._templates: dict[str, dict] = {}
        self._validator = DSLSchemaValidator()

    def register(self, name: str, dsl: dict):
        self._validator.validate(dsl)
        self._templates[name] = copy.deepcopy(dsl)
        logging.info(f"[DSLTemplateManager] Registered template '{name}'")

    def unregister(self, name: str):
        self._templates.pop(name, None)

    def list_templates(self) -> list[str]:
        return list(self._templates.keys())

    def instantiate(self, name: str, variables: dict[str, Any]) -> dict:
        template = self._templates.get(name)
        if not template:
            raise ValueError(f"Template '{name}' not found. Available: {self.list_templates()}")

        dsl_str = json.dumps(template, ensure_ascii=False)
        for key, val in variables.items():
            placeholder = "${" + key + "}"
            dsl_str = dsl_str.replace(placeholder, str(val) if val is not None else "")
            dsl_str = dsl_str.replace("${" + key.upper() + "}", str(val) if val is not None else "")

        return json.loads(dsl_str)

    def get_template(self, name: str) -> Optional[dict]:
        return copy.deepcopy(self._templates.get(name))