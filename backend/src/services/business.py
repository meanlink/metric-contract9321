"""Safe, dimension-aware aggregate metric DSL and source-ledger reconciliation."""
import ast
from datetime import date, timedelta
import math
import random


MONEY = {"gross", "refunds", "discount", "recognized_net"}
FIELDS = MONEY | {"visits"}
DEFAULT_EXPRESSION = "sum(gross)-sum(refunds)-sum(discount)"
PER_VISIT = "ratio(sum(gross)-sum(refunds)-sum(discount),sum(visits))"


def metadata():
    return {
        "name": "净收入口径对账台", "short": "metric-contract", "tagline": "给口径加上单位与分母约束，再把源账差额追到渠道",
        "client": "模拟客户：星禾数字内容财务与经营分析组，4 名财务、3 名数据分析师。",
        "workflow": "渠道日报使用各自的净收入公式，财务每周手工对照账面认可额，经营分析再拼成例会报表。",
        "pains": ["退款和折扣的扣除时点不一致", "人均指标先取平均造成分母偏差", "自由公式缺少字段白名单、单位和零分母校验"],
        "upstream": "渠道日汇总账和财务认可净额（模拟数据，统一CNY）", "downstream": "财务口径复核清单与经营周报。",
        "entities": ["源账切片", "口径对账运行", "财务分发计划"], "dataset_name": "星禾_渠道日汇总_净收入口径.csv",
        "columns": [{"key": "day", "label": "日期", "type": "string"}, {"key": "channel", "label": "渠道", "type": "string"}, {"key": "currency", "label": "币种", "type": "string"}, {"key": "gross", "label": "含折扣前收入", "type": "number"}, {"key": "refunds", "label": "退款", "type": "number"}, {"key": "discount", "label": "折扣", "type": "number"}, {"key": "recognized_net", "label": "源账认可净额", "type": "number"}, {"key": "visits", "label": "有效访问", "type": "number"}],
        "parameters": [{"key": "expression", "label": "指标DSL", "type": "select", "default": DEFAULT_EXPRESSION, "options": [{"value": DEFAULT_EXPRESSION, "label": "净收入 = 毛额−退款−折扣"}, {"value": PER_VISIT, "label": "单次访问净收入 = 净收入/访问量"}]}, {"key": "group_by", "label": "对账维度", "type": "select", "default": "channel", "options": [{"value": "channel", "label": "按渠道"}, {"value": "week", "label": "按自然周"}]}, {"key": "zero_policy", "label": "零分母策略", "type": "select", "default": "reject", "options": [{"value": "reject", "label": "阻止计算并修复"}, {"value": "zero", "label": "明确记0并警告"}]}],
        "workbench_title": "口径契约与维度对账", "action_label": "校验口径并执行对账", "unit": "CNY", "accent": "oklch(46% 0.08 75)", "style": "finance-ledger",
        "seed_story": ["08:30 上传星禾_渠道日汇总_净收入口径.csv，共60天×3渠道180行。", "08:45 确认所有金额已统一为CNY，gross为扣折扣前金额。", "09:00 选择净收入DSL，按渠道汇总。", "09:10 比较源账认可净额与统一口径差额。", "09:30 追到合作渠道额外扣减的服务费，保留复核证据。", "10:00 切换单次访问净收入，使用总净额除以总访问量。", "15:00 输出差异报表并设置财务例会本地分发计划。"],
        "method": "只解析AST白名单：sum(已注册字段)、ratio、四则运算和数值常量；不执行eval。加减必须同单位，乘法仅允许无量纲比例，分母为0依策略拒绝或显式记0。先汇总分子分母再计算比率。",
        "limitations": ["仅支持统一币种的日汇总账，不连接真实财务账或自动记账。", "差额用于发现口径或源账差异，不代表审计确认结论；净额不包含税费与汇兑。"],
    }


def seed_records():
    rng = random.Random(1018)
    rows = []
    start = date(2026, 9, 1)
    for index in range(60):
        for channel, base in (("直营内容", 18500), ("机构许可", 11600), ("合作渠道", 14200)):
            gross = round(base * rng.uniform(0.78, 1.25), 2)
            refunds = round(gross * rng.uniform(0.015, 0.055), 2)
            discount = round(gross * rng.uniform(0.04, 0.12), 2)
            service_fee = round(gross * 0.018, 2) if channel == "合作渠道" else 0
            rows.append({"day": str(start + timedelta(days=index)), "channel": channel, "currency": "CNY", "gross": gross, "refunds": refunds, "discount": discount, "recognized_net": round(gross-refunds-discount-service_fee, 2), "visits": rng.randint(180, 800)})
    return rows


def _parse(expression):
    if not isinstance(expression, str) or not 1 <= len(expression) <= 300:
        raise ValueError("DSL须为1至300字的公式")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        raise ValueError("指标DSL语法错误") from None
    if len(list(ast.walk(tree))) > 60:
        raise ValueError("指标DSL过于复杂")
    return tree.body


def _evaluate(node, rows, zero_policy, warnings):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        value = float(node.value)
        if not math.isfinite(value):
            raise ValueError("DSL常量必须为有限数值")
        return value, "scalar"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        if node.func.id == "sum" and len(node.args) == 1 and isinstance(node.args[0], ast.Name):
            field = node.args[0].id
            if field not in FIELDS:
                raise ValueError(f"未注册的指标字段：{field}")
            return sum(row[field] for row in rows), "CNY" if field in MONEY else "visit"
        if node.func.id == "ratio" and len(node.args) == 2:
            return _divide(_evaluate(node.args[0], rows, zero_policy, warnings), _evaluate(node.args[1], rows, zero_policy, warnings), zero_policy, warnings)
        raise ValueError("DSL只允许sum(注册字段)与ratio(分子,分母)")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value, unit = _evaluate(node.operand, rows, zero_policy, warnings)
        return (-value if isinstance(node.op, ast.USub) else value), unit
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, rows, zero_policy, warnings)
        right = _evaluate(node.right, rows, zero_policy, warnings)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left[1] != right[1]:
                raise ValueError(f"口径单位不一致：{left[1]}不能与{right[1]}加减")
            return (left[0] + right[0] if isinstance(node.op, ast.Add) else left[0] - right[0]), left[1]
        if isinstance(node.op, ast.Div):
            return _divide(left, right, zero_policy, warnings)
        if isinstance(node.op, ast.Mult):
            if left[1] != "scalar" and right[1] != "scalar":
                raise ValueError("乘法只能用于一个无量纲比例，禁止金额×访问数")
            return left[0] * right[0], right[1] if left[1] == "scalar" else left[1]
    raise ValueError("DSL包含未允许的结构；禁止属性、索引、外部函数和代码执行")


def _divide(left, right, policy, warnings):
    if right[0] == 0:
        if policy == "reject":
            raise ValueError("指标分母为0，请修复源账或明确选择记0策略")
        warnings.append("至少一个分组分母为0；按显式策略记0，不能据此判断经营表现。")
        value = 0.0
    else:
        value = left[0] / right[0]
    unit = "scalar" if left[1] == right[1] else left[1] if right[1] == "scalar" else f"{left[1]}/{right[1]}"
    return value, unit


def _r4(value):
    return None if value is None else round(float(value), 4)


def _week_label(day):
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02}"


def _align(rows, key, target):
    """Put the 4-decimal rounding residual on the largest row so the column visibly adds up."""
    present = [row for row in rows if isinstance(row.get(key), (int, float))]
    if not present:
        return
    observed = round(sum(row[key] for row in present), 4)
    residual = round(round(target, 4) - observed, 4)
    if residual:
        pivot = max(present, key=lambda row: abs(row[key]))
        pivot[key] = round(pivot[key] + residual, 4)


def _row_breakdown(row, numerator):
    return {
        "gross": _r4(sum(r["gross"] for r in row)), "refunds": _r4(sum(r["refunds"] for r in row)),
        "discount": _r4(sum(r["discount"] for r in row)), "visits": int(sum(r["visits"] for r in row)),
        "computed_amount": _r4(numerator), "source_amount": _r4(sum(r["recognized_net"] for r in row)),
        "amount_difference": _r4(numerator - sum(r["recognized_net"] for r in row)),
    }


def analyze(records, params):
    if not isinstance(records, list) or not records:
        raise ValueError("请先导入渠道日汇总账")
    grouping = params.get("group_by", "channel")
    zero_policy = params.get("zero_policy", "reject")
    if grouping not in ("channel", "week") or zero_policy not in ("reject", "zero"):
        raise ValueError("未知的分组或零分母策略")
    expression = params.get("expression", DEFAULT_EXPRESSION)
    tree = _parse(expression)
    rows = []
    currencies, natural_keys = set(), set()
    for record in records:
        try:
            day = date.fromisoformat(str(record["day"]))
            channel = str(record["channel"]).strip()
            currency = str(record["currency"])
            values = {key: float(record[key]) for key in FIELDS}
        except (KeyError, TypeError, ValueError):
            raise ValueError("源账缺少日期、渠道、币种或注册数值字段") from None
        # 毛额/退款/折扣/访问是流量约束，不能为负；源账认可净额允许为负，便于暴露退款倒挂。
        non_negative = all(math.isfinite(values[key]) and 0 <= values[key] <= 1_000_000_000 for key in ("gross", "refunds", "discount", "visits"))
        if not channel or not non_negative or not (math.isfinite(values["recognized_net"]) and -1_000_000_000 <= values["recognized_net"] <= 1_000_000_000):
            raise ValueError("渠道不能为空；毛额、退款、折扣和访问数须为0至10亿的有限数值，源账认可净额允许为负但须在±10亿内")
        if not values["visits"].is_integer():
            raise ValueError("访问数必须为整数，不能提交已经平均化的分母")
        key = (day, channel)
        if key in natural_keys:
            raise ValueError("渠道日自然键重复，请先完成源账去重")
        natural_keys.add(key)
        currencies.add(currency)
        values.update({"day": day, "channel": channel, "week": _week_label(day)})
        rows.append(values)
    if currencies != {"CNY"}:
        raise ValueError("当前口径只接受统一CNY，混合或其他币种请先换算")
    warnings = []
    total_metric, unit = _evaluate(tree, rows, zero_policy, warnings)
    if unit not in ("CNY", "CNY/visit"):
        raise ValueError("当前对账契约只接受净收入CNY或单次访问净收入CNY/visit")
    ratio_mode = unit == "CNY/visit"
    if ratio_mode:
        if not (isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name) and tree.func.id == "ratio" and len(tree.args) == 2):
            raise ValueError("单次访问口径需使用顶层 ratio(金额,访问) 结构，才能拆分金额差额与总比率贡献")
        amount_node, denominator_node = tree.args
        amount_total, amount_unit = _evaluate(amount_node, rows, zero_policy, warnings)
        denominator_total, denominator_unit = _evaluate(denominator_node, rows, zero_policy, warnings)
        if amount_unit != "CNY" or denominator_unit != "visit":
            raise ValueError("ratio 的分子必须是CNY金额，分母必须是访问数")
    else:
        amount_node, denominator_node = tree, None
        amount_total, denominator_total = total_metric, None
    source_amount_total = sum(row["recognized_net"] for row in rows)
    total_amount_difference = amount_total - source_amount_total
    if ratio_mode:
        source_metric_total = 0.0 if denominator_total == 0 else source_amount_total / denominator_total
    else:
        source_metric_total = source_amount_total
    if not all(math.isfinite(value) for value in (total_metric, source_metric_total, amount_total, source_amount_total)):
        raise ValueError("指标结果非有限值，请缩小数值范围或修复口径")
    discrepancy = total_metric - source_metric_total

    def contributions(amount_difference):
        if not ratio_mode:
            return amount_difference
        if not denominator_total:
            # 总访问为0且按显式记0策略执行：整列比率无定义，贡献统一记0并保留警告。
            return 0.0
        return amount_difference / denominator_total

    def build(keyfunc, with_metric, strict):
        groups = {}
        for row in rows:
            groups.setdefault(keyfunc(row), []).append(row)
        output = []
        for key in sorted(groups):
            group = groups[key]
            numerator, group_unit = _evaluate(amount_node, group, zero_policy, warnings)
            if group_unit != "CNY":
                raise ValueError("分组间金额单位不一致")
            item = _row_breakdown(group, numerator)
            if isinstance(key, tuple):
                item.update({"channel": key[0], "week": key[1], "dimension": f"{key[0]} · {key[1]}"})
            elif keyfunc is _week_key:
                item["week"] = key
            else:
                item["channel"] = key
            if not isinstance(key, tuple):
                item["dimension"] = key
            if ratio_mode and with_metric:
                # 分层下钻用该组自身分母；只有主汇总层（strict）保留注册的零分母拒绝契约，
                # 交叉层出现零访问时标记为无定义而不是让整次对账失败。
                group_denominator = float(_evaluate(denominator_node, group, "zero", [])[0])
                item["denominator"] = _r4(group_denominator)
                if group_denominator == 0:
                    if strict:
                        computed, _ = _divide((numerator, "CNY"), (0.0, "visit"), zero_policy, warnings)
                    else:
                        computed = 0.0 if zero_policy == "zero" else None
                    item["computed_metric"] = _r4(computed)
                    item["source_metric"] = _r4(computed)
                    item["local_difference"] = _r4(computed)
                else:
                    item["computed_metric"] = _r4(numerator / group_denominator)
                    item["source_metric"] = _r4(item["source_amount"] / group_denominator)
                    item["local_difference"] = _r4(item["computed_metric"] - item["source_metric"])
            elif with_metric:
                item["denominator"] = None
                item["computed_metric"] = item["computed_amount"]
                item["source_metric"] = item["source_amount"]
                item["local_difference"] = item["amount_difference"]
            output.append(item)
        _align(output, "amount_difference", total_amount_difference)
        if ratio_mode:
            for item in output:
                item["contribution"] = _r4(contributions(item["amount_difference"]))
            _align(output, "contribution", discrepancy)
        else:
            for item in output:
                item["contribution"] = item["amount_difference"]
        for item in output:
            item["share"] = None if not discrepancy else _r4(item["contribution"] / discrepancy)
        return output

    def _week_key(row):
        return row["week"]

    channel_rows = build(lambda row: row["channel"], True, grouping == "channel")
    week_rows = build(_week_key, True, grouping == "week")
    cross_rows = build(lambda row: (row["channel"], row["week"]), True, False)

    ledger = []
    for row in rows:
        numerator = float(_evaluate(amount_node, [row], zero_policy, warnings)[0])
        item = {
            "day": str(row["day"]), "week": row["week"], "channel": row["channel"],
            "gross": _r4(row["gross"]), "refunds": _r4(row["refunds"]), "discount": _r4(row["discount"]),
            "visits": int(row["visits"]), "computed_amount": _r4(numerator),
            "source_amount": _r4(row["recognized_net"]), "amount_difference": _r4(numerator - row["recognized_net"]),
        }
        item["contribution"] = _r4(contributions(item["amount_difference"]))
        notes = []
        if row["visits"] == 0:
            notes.append("零访问：金额差额计入分子，但不增加分母")
        if item["computed_amount"] < 0 or item["source_amount"] < 0:
            notes.append("负净收入：退款与折扣超过毛额或源账为负，未按0截断")
        if item["amount_difference"] > 0:
            notes.append("源账低于统一口径，需回查额外扣减")
        elif item["amount_difference"] < 0:
            notes.append("源账高于统一口径")
        item["note"] = "；".join(notes) or "无差异"
        ledger.append(item)
    _align(ledger, "amount_difference", total_amount_difference)
    if ratio_mode:
        for item in ledger:
            item["contribution"] = _r4(contributions(item["amount_difference"]))
        _align(ledger, "contribution", discrepancy)
    else:
        for item in ledger:
            item["contribution"] = item["amount_difference"]

    zero_rows = [item for item in ledger if item["visits"] == 0]
    negative_rows = [item for item in ledger if item["computed_amount"] < 0 or item["source_amount"] < 0]
    zero_groups = [item for item in cross_rows if item["visits"] == 0]
    contribution_sum = round(sum(item["contribution"] for item in ledger if item["contribution"] is not None), 4)
    amount_sum = round(sum(item["amount_difference"] for item in ledger), 4)
    target_contribution = round(discrepancy, 4)
    target_amount = round(total_amount_difference, 4)

    column_sets = {
        "channel": [{"key": "dimension", "label": "渠道"}, {"key": "gross", "label": "含折扣前收入"}, {"key": "refunds", "label": "退款"}, {"key": "discount", "label": "折扣"}, {"key": "visits", "label": "有效访问（分母）"}, {"key": "computed_amount", "label": "统一口径金额"}, {"key": "source_amount", "label": "源账认可金额"}, {"key": "amount_difference", "label": "金额差额"}, {"key": "computed_metric", "label": "统一口径单次净收入"}, {"key": "source_metric", "label": "源账单次净收入"}, {"key": "local_difference", "label": "组内单次差额（不可相加）"}, {"key": "contribution", "label": "对总差额贡献"}],
        "week": [{"key": "dimension", "label": "自然周"}, {"key": "gross", "label": "含折扣前收入"}, {"key": "refunds", "label": "退款"}, {"key": "discount", "label": "折扣"}, {"key": "visits", "label": "有效访问（分母）"}, {"key": "computed_amount", "label": "统一口径金额"}, {"key": "source_amount", "label": "源账认可金额"}, {"key": "amount_difference", "label": "金额差额"}, {"key": "computed_metric", "label": "统一口径单次净收入"}, {"key": "source_metric", "label": "源账单次净收入"}, {"key": "local_difference", "label": "组内单次差额（不可相加）"}, {"key": "contribution", "label": "对总差额贡献"}],
        "cross": [{"key": "channel", "label": "渠道"}, {"key": "week", "label": "自然周"}, {"key": "gross", "label": "含折扣前收入"}, {"key": "refunds", "label": "退款"}, {"key": "discount", "label": "折扣"}, {"key": "visits", "label": "有效访问（分母）"}, {"key": "computed_amount", "label": "统一口径金额"}, {"key": "source_amount", "label": "源账认可金额"}, {"key": "amount_difference", "label": "金额差额"}, {"key": "computed_metric", "label": "统一口径单次净收入"}, {"key": "source_metric", "label": "源账单次净收入"}, {"key": "contribution", "label": "对总差额贡献"}],
        "ledger": [{"key": "day", "label": "日期"}, {"key": "week", "label": "自然周"}, {"key": "channel", "label": "渠道"}, {"key": "gross", "label": "含折扣前收入"}, {"key": "refunds", "label": "退款"}, {"key": "discount", "label": "折扣"}, {"key": "visits", "label": "有效访问"}, {"key": "computed_amount", "label": "统一口径金额"}, {"key": "source_amount", "label": "源账认可金额"}, {"key": "amount_difference", "label": "金额差额"}, {"key": "contribution", "label": "对总差额贡献"}, {"key": "note", "label": "追溯说明"}],
    }
    if not ratio_mode:
        for columns in column_sets.values():
            for column in columns:
                if column["key"] == "computed_metric":
                    column["label"] = "统一口径净收入"
                elif column["key"] == "source_metric":
                    column["label"] = "源账认可净收入"
                elif column["key"] == "local_difference":
                    column["label"] = "组内金额差额"
                elif column["key"] == "contribution":
                    column["label"] = "对总差额贡献（同金额差额）"

    tables = {
        "channel": {"key": "channel", "title": "按渠道追溯", "note": "各渠道金额差额与贡献；单次差额仅用于组内解释，不能直接相加。", "columns": column_sets["channel"], "rows": channel_rows},
        "week": {"key": "week", "title": "按自然周追溯", "note": "自然周为ISO周（周一至周日）；跨渠道合并后同样保留收入、退款、折扣与访问分母。", "columns": column_sets["week"], "rows": week_rows},
        "cross": {"key": "cross", "title": "渠道 × 自然周交叉追溯", "note": "交叉行是源账最细的可复核汇总层；每行贡献合计等于总差额。", "columns": column_sets["cross"], "rows": cross_rows},
        "ledger": {"key": "ledger", "title": "源账逐行追溯", "note": "逐行列出源账日期、渠道与自然周，直接定位造成差额的原始记录。", "columns": column_sets["ledger"], "rows": ledger},
    }
    order = ["week", "channel"] if grouping == "week" else ["channel", "week"]
    order += ["cross", "ledger"]
    table_list = [tables[key] for key in order]

    primary_label = "渠道" if grouping == "channel" else "自然周"
    zero_explanation = (
        "本次源账没有零访问记录。" if not zero_rows else
        f"有 {len(zero_rows)} 条零访问记录：这些行的收入、退款、折扣仍计入金额分子，但不增加访问分母，"
        "因此会抬高或拉低整体单次净收入；其单次指标无定义，组内访问合计为0时该组单次指标也无定义。"
        f"当前零分母策略为{'阻止计算并修复' if zero_policy == 'reject' else '明确记0并警告'}。"
    )
    negative_explanation = (
        "本次源账没有负净收入记录。" if not negative_rows else
        f"有 {len(negative_rows)} 条负净收入记录：退款与折扣超过毛额，或源账认可净额为负。"
        "结果按实际负值参与汇总，未按0截断，也不能把负的单次净收入解释为增长。"
    )
    findings = [
        f"执行公式：{expression}；单位契约：{unit}；零分母策略：{zero_policy}。",
        "单次访问净收入下，各渠道自身的单次差额不能相加（分母不同）。可加的是金额差额，以及对总比率差额的贡献＝组金额差额÷总访问分母；本结果明细贡献合计与总差额一致。"
        if ratio_mode else "金额口径下，对总差额的贡献就是金额差额；渠道、自然周、交叉与源账逐行四个层级的贡献合计均与总差额一致。",
        "模拟种子的合作渠道额外扣减1.8%服务费，用于展示一种可解释差异；差额本身仍须回查原账，不代表审计结论。",
        zero_explanation,
        negative_explanation,
        "展示值保留4位小数，四舍五入尾差统一落在贡献绝对值最大的一行，因此明细相加与总差额在展示精度上完全一致。",
    ]
    contribution_consistent = contribution_sum == target_contribution
    amount_consistent = amount_sum == target_amount
    summary = (
        f"按{primary_label}对账{len(rows)}行；统一口径{total_metric:,.4f}{unit}，源账认可{source_metric_total:,.4f}{unit}，"
        f"总差额{discrepancy:+,.4f}{unit}；明细贡献合计{contribution_sum:+,.4f}{unit}（{'一致' if contribution_consistent else '不一致'}）。"
        f"零访问{len(zero_rows)}行，负净收入{len(negative_rows)}行。"
    )
    return {
        "summary": summary,
        "metrics": [{"label": "统一口径", "value": round(total_metric, 4), "unit": unit}, {"label": "源账认可", "value": round(source_metric_total, 4), "unit": unit}, {"label": "总差额", "value": round(discrepancy, 4), "unit": unit}, {"label": "明细贡献合计", "value": contribution_sum, "unit": unit}],
        "table": table_list[0],
        "tables": table_list,
        "chart": {"type": "bar", "labels": [row["dimension"] for row in (channel_rows if grouping == "channel" else week_rows)], "series": [{"name": "统一口径", "values": [row["computed_metric"] for row in (channel_rows if grouping == "channel" else week_rows)]}, {"name": "源账认可", "values": [row["source_metric"] for row in (channel_rows if grouping == "channel" else week_rows)]}]},
        "checks": {
            "unit": unit, "total_difference": target_contribution, "contribution_sum": contribution_sum,
            "total_amount_difference": target_amount, "amount_difference_sum": amount_sum,
            "contribution_consistent": contribution_consistent, "amount_difference_consistent": amount_consistent,
            "rounding_residual": 0.0 if contribution_consistent else round(target_contribution - contribution_sum, 4),
        },
        "explanations": {
            "zero_visits": {"count": len(zero_rows), "groups": len(zero_groups), "rows": zero_rows, "explanation": zero_explanation},
            "negative_net": {"count": len(negative_rows), "rows": negative_rows, "explanation": negative_explanation},
        },
        "findings": findings,
        "method": metadata()["method"], "warnings": list(dict.fromkeys(warnings)),
    }
