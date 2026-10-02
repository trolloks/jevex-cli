import unittest

from benchmark import TASKS, delta, estimated_cost, grade


class BenchmarkTests(unittest.TestCase):
    def test_grader_and_cumulative_usage(self):
        examples = [
            "def slugify(text):\n return '-'.join(''.join(c.lower() if c.isalnum() else ' ' for c in text).split())",
            "def split_csv_row(line):\n out=[]; field=''; quoted=False; i=0\n while i<len(line):\n  c=line[i]\n  if c=='\"':\n   if quoted and i+1<len(line) and line[i+1]=='\"': field+='\"'; i+=1\n   else: quoted=not quoted\n  elif c==',' and not quoted: out.append(field); field=''\n  else: field+=c\n  i+=1\n if quoted: raise ValueError()\n out.append(field)\n return out",
            "def schedule_tasks(deps):\n if any(d not in deps for values in deps.values() for d in values): raise ValueError()\n out=[]\n while len(out)<len(deps):\n  ready=sorted(n for n,values in deps.items() if n not in out and all(d in out for d in values))\n  if not ready: raise ValueError()\n  out.append(ready[0])\n return out",
        ]
        for task, answer in zip(TASKS, examples):
            self.assertEqual(grade(task, answer), (len(task["cases"]), ""))
        self.assertEqual(grade(TASKS[3], "def evaluate_expression(text):\n return ord('7') - ord('0')"), (1, ""))
        self.assertEqual(delta({"input_tokens": 80, "cached_input_tokens": 50},
                               {"input_tokens": 30, "cached_input_tokens": 10}),
                         {"input_tokens": 50, "cached_input_tokens": 40})
        self.assertAlmostEqual(estimated_cost("gpt-6-luna", {"input_tokens": 100, "cached_input_tokens": 50,
                                                               "cache_write_input_tokens": 0, "output_tokens": 10}), .0000105)


if __name__ == "__main__":
    unittest.main()
