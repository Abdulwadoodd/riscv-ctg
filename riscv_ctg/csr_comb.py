# See LICENSE.incore for details
import re

from riscv_ctg.log import logger
from riscv_ctg.constants import *

import tokenize as tkn
from io import BytesIO

OPS = ['not', 'and', 'or']
OP_PRIORITY = {
    'not': -1,
    'and': -2,
    'or' : -3,
}

CSR_REGS = ['mvendorid', 'marchid', 'mimpid', 'mhartid', 'mstatus', 'misa', 'medeleg', 'mideleg', 'mie', 'mtvec', 'mcounteren', 'mscratch', 'mepc', 'mcause', 'mtval', 'mip', 'pmpcfg0', 'pmpcfg1', 'pmpcfg2', 'pmpcfg3', 'mcycle', 'minstret', 'mcycleh', 'minstreth', 'mcountinhibit', 'tselect', 'tdata1', 'tdata2', 'tdata3', 'dcsr', 'dpc', 'dscratch0', 'dscratch1', 'sstatus', 'sedeleg', 'sideleg', 'sie', 'stvec', 'scounteren', 'sscratch', 'sepc', 'scause', 'stval', 'sip', 'satp', 'vxsat', 'fflags', 'frm', 'fcsr']

csr_comb_covpt_regex_string = f'({"|".join(CSR_REGS)})' + r' *& *([^ ].*)== *([^ ].*)'
csr_comb_covpt_regex = re.compile(csr_comb_covpt_regex_string)

def tokenize(s):
    result = []
    g = tkn.tokenize(BytesIO(s.encode('utf-8')).readline)
    for tok_num, tok_val, _, _, _ in g:
        if tok_num in [tkn.ENCODING, tkn.NEWLINE, tkn.ENDMARKER]:
            continue
        result.append((tok_num, tok_val))
    return result

def untokenize(tokens):
    return tkn.untokenize(tokens)

# a dummy class acting as an interface for boolean expressions
class BooleanExpression:
    def SAT(self):
        # returns the complete list of solutions for this expression's satisfiability
        # a single solution is a tuple of two lists:
        #  - the literals in the first list must evaluate to true
        #  - the literals in the second list must evaluate to false
        raise Exception("not implemented")

    def __str__(self):
        raise Exception("not implemented")

class NotExpression(BooleanExpression):
    def __init__(self, operand):
        self.operand = operand

    def SAT(self):
        return [(operand_f, operand_t) for operand_t, operand_f in self.operand.SAT()]

    def __str__(self):
        return f'not ({str(self.operand)})'

class AndExpression(BooleanExpression):
    def __init__(self, lhs, rhs):
        self.lhs = lhs
        self.rhs = rhs

    def SAT(self):
        return [(lhs_t + rhs_t, lhs_f + rhs_f) for lhs_t, lhs_f in self.lhs.SAT() for rhs_t, rhs_f in self.rhs.SAT()]

    def __str__(self):
        return f'({str(self.lhs)}) and ({str(self.rhs)})'

class OrExpression(BooleanExpression):
    def __init__(self, lhs, rhs):
        self.lhs = lhs
        self.rhs = rhs

    def SAT(self):
        lhs_SAT = self.lhs.SAT()
        rhs_SAT = self.rhs.SAT()

        sols = []
        for lhs_t, lhs_f in lhs_SAT:
            for rhs_t, rhs_f in rhs_SAT:
                sols.extend([
                    (lhs_t + rhs_f, lhs_f + rhs_t),
                    (lhs_f + rhs_t, lhs_t + rhs_f),
                    (lhs_t + rhs_t, lhs_f + rhs_f),
                ])

        return sols

    def __str__(self):
        return f'({str(self.lhs)}) or ({str(self.rhs)})'

class LiteralExpression(BooleanExpression):
    def __init__(self, val):
        self.val = val

    def SAT(self):
        return [([self.val], [])]

    def __str__(self):
        return str(self.val)

def parse_csr_covpt(covpt):
    toks = tokenize(covpt)

    bracket_depth = 0
    clause_depths = []
    for tok_num, tok_val in toks:
        if tok_val == '(':
            bracket_depth += 1
        elif tok_val == ')':
            bracket_depth -= 1
        elif tok_val == '==':
            clause_depths.append(bracket_depth)

    bracket_depth = 0
    operator_stack = []
    clause_stack = []
    clause_index = 0
    current_clause = []
    current_clause_depth = clause_depths[clause_index]
    for tok_num, tok_val in toks:
        if tok_val == '(':
            bracket_depth += 1

            if bracket_depth > current_clause_depth:
                current_clause.append((tok_num, tok_val))
            else:
                operator_stack.append('(')
        elif tok_val == ')':
            bracket_depth -= 1

            if current_clause:
                if bracket_depth < current_clause_depth:
                    clause_stack.append(LiteralExpression(untokenize(current_clause)))
                    while operator_stack[-1] != '(':
                        op = operator_stack.pop()
                        if op == 'not':
                            operand = clause_stack.pop()
                            clause_stack.append(NotExpression(operand))
                        else:
                            rhs = clause_stack.pop()
                            lhs = clause_stack.pop()
                            clause_stack.append(AndExpression(lhs, rhs) if op == 'and' else OrExpression(lhs, rhs))
                    operator_stack.pop()

                    current_clause = []
                    clause_index += 1
                    if (clause_index >= len(clause_depths)):
                        break
                    current_clause_depth = clause_depths[clause_index]
                else:
                    current_clause.append((tok_num, tok_val))
            else:
                while operator_stack[-1] != '(':
                    op = operator_stack.pop()
                    if op == 'not':
                        operand = clause_stack.pop()
                        clause_stack.append(NotExpression(operand))
                    else:
                        rhs = clause_stack.pop()
                        lhs = clause_stack.pop()
                        clause_stack.append(AndExpression(lhs, rhs) if op == 'and' else OrExpression(lhs, rhs))
                operator_stack.pop()
        elif tok_val in OPS:
            if current_clause:
                clause_stack.append(LiteralExpression(untokenize(current_clause)))
                current_clause = []
                clause_index += 1
                current_clause_depth = clause_depths[clause_index]

            # prioritize not over and over or
            while len(operator_stack) > 0 and operator_stack[-1] in OPS and OP_PRIORITY[operator_stack[-1]] > OP_PRIORITY[tok_val]:
                op = operator_stack.pop()
                if op == 'not':
                    operand = clause_stack.pop()
                    clause_stack.append(NotExpression(operand))
                else:
                    rhs = clause_stack.pop()
                    lhs = clause_stack.pop()
                    clause_stack.append(AndExpression(lhs, rhs) if op == 'and' else OrExpression(lhs, rhs))

            operator_stack.append(tok_val)
        else:
            current_clause.append((tok_num, tok_val))

    if current_clause:
        clause_stack.append(LiteralExpression(untokenize(current_clause)))

    while len(operator_stack) > 0:
        op = operator_stack.pop()
        if op == 'not':
            operand = clause_stack.pop()
            clause_stack.append(NotExpression(operand))
        else:
            rhs = clause_stack.pop()
            lhs = clause_stack.pop()
            clause_stack.append(AndExpression(lhs, rhs) if op == 'and' else OrExpression(lhs, rhs))

    bool_expr = clause_stack.pop()
    return bool_expr

class GeneratorCSRComb():
    '''
    A class to generate RISC-V assembly tests for CSR-combination coverpoints.
    '''

    def __init__(self, base_isa, xlen, randomize):
        self.base_isa = base_isa
        self.xlen = xlen
        self.randomize = randomize

    def csr_comb(self, cgf_node):
        logger.debug('Generating tests for csr_comb')
        if 'csr_comb' in cgf_node:
            csr_comb = set(cgf_node['csr_comb'])
        else:
            return

        # This function extracts the csr register, the field mask and the field value from the coverpoint
        # The coverpoint is assumed of the format: 'csr_reg & mask == val'
        # csr_reg must be a valid csr register; mask and val are allowed to be valid python expressions
        def get_csr_reg_field_mask_and_val(coverpoint):
            regex_match = csr_comb_covpt_regex.match(coverpoint.strip())
            if regex_match is None:
                return None, None, None
            csr_reg, mask_expr, val_expr = regex_match.groups()
            mask = eval(mask_expr)
            val = eval(val_expr)
            return csr_reg, mask, val

        temp_regs = ['x28', 'x29'] # t0 and t1
        dest_reg = 'x23'

        instr_dict = []
        offset = 0
        for covpt in csr_comb:
            csr_reg, mask, val = get_csr_reg_field_mask_and_val(covpt)
            if csr_reg is None:
                logger.error(f'Invalid csr_comb coverpoint: {covpt}')
                continue
            instr_dict.append({
                'csr_reg': csr_reg, 'mask': hex(mask), 'val': hex(val), 'dest_reg': dest_reg,
                'temp_reg1': temp_regs[0], 'temp_reg2': temp_regs[1], 'offset': offset
            })
            offset += 4

        return instr_dict

    def write_test(self, fprefix, cgf_node, usage_str, cov_label, instr_dict):
        base_reg = 'x8'

        code = [""]
        data = [".align 4","rvtest_data:",".word 0xbabecafe", \
                ".word 0xabecafeb", ".word 0xbecafeba", ".word 0xecafebab"]
        sig = [""]

        sig_label = f"signature_{base_reg}_0"
        sig.append(signode_template.safe_substitute(label = sig_label, n = len(instr_dict), sz = 'XLEN/32'))
        code.append(f"RVTEST_SIGBASE({base_reg}, {sig_label})\n")

        for i, instr in enumerate(instr_dict):
            code.extend([
                f"\ninst_{i}:",
                csr_reg_write_test_template.safe_substitute({
                    'base_reg': base_reg, **instr
                })
            ])

        case_str = ''.join([case_template.safe_substitute(xlen = self.xlen, num = i, cov_label = cov_label) for i, cond in enumerate(cgf_node.get('config', []))])
        test_str = part_template.safe_substitute(case_str = case_str, code = '\n'.join(code))

        with open(fprefix + '_csr-comb.S', 'w') as fp:
            fp.write(usage_str + csr_comb_test_template.safe_substitute(
                isa = self.base_isa.upper(), # how to get the extensions?
                test = test_str,
                data = '\n'.join(data),
                sig = '\n'.join(sig),
                label = cov_label
            ))
