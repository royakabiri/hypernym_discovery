import glob, os, re, logging, shutil
import numpy as np
import torch
from torch.utils.data import TensorDataset

logger = logging.getLogger(__name__)

PAD_TOKEN=0
SEGMENT_ID=0
MASK_PADDING_WITH_ZERO=True

def make_candidate_set(opt, tokenizer, candidate_data):
    """ Make unlabeled dataset for candidates.
    Args:
    - opt:
    - tokenizer:
    - candidate_data: dict containing candidates (list)

    """
    candidates = candidate_data['candidates']
    return make_q_or_c_dataset(opt, tokenizer, candidates, verbose=True)


def make_test_set(opt, tokenizer, test_data):
    """ Make unlabeled dataset for test set.
    Args:
    - opt:
    - tokenizer:
    - test_data: dict containing queries (list)

    """
    queries = test_data["queries"]
    return make_q_or_c_dataset(opt, tokenizer, queries, verbose=False)


def make_train_set(opt, tokenizer, train_data, max_pos_ratio=0.5, verbose=False):
    """ Make labeled dataset for training set. Subsample candidates using negative sampling.
    Args:
    - opt:
    - tokenizer: 
    - train_data: dict containing queries (list), candidates (list), and gold_hypernym_candidate_ids (list of lists, one per query)

    """
    if max_pos_ratio <=0 or max_pos_ratio > 1:
        msg = "max_pos_ratio must be in (0,1]"
        raise ValueError(msg)
    
    # Load training data
    queries = train_data["queries"]
    candidates = train_data["candidates"]
    gold_cand_ids = train_data["gold_hypernym_candidate_ids"]

    # Shuffle positive examples
    for i in range(len(gold_cand_ids)):
        np.random.shuffle(gold_cand_ids[i])

    # Subsample positive examples if necessary
    max_pos = int(max_pos_ratio * opt.per_query_nb_examples)
    nb_pos_discarded = 0
    for i in range(len(gold_cand_ids)):
        pos = gold_cand_ids[i]
        if len(pos) > max_pos:
            kept = pos[:max_pos]
            nb_pos_discarded += len(pos) - len(kept)
            gold_cand_ids[i] = kept
    if nb_pos_discarded > 0 and verbose:
        msg = "  {} positive hypernyms removed because the query had more than {}".format(nb_pos_discarded, opt.per_query_nb_examples)
        logger.warning(msg)

    # Sample negative examples for training
    all_cand_ids = list(range(len(candidates)))
    neg_cand_ids = sample_negative_examples(all_cand_ids, gold_cand_ids, opt.per_query_nb_examples, verbose=verbose)
    cand_ids = []
    labels = []
    for i in range(len(queries)):
        xy = [(x,1) for x in gold_cand_ids[i]] + [(x,0) for x in neg_cand_ids[i]]
        np.random.shuffle(xy)
        x,y = zip(*xy)
        cand_ids.append(x)
        labels.append(y)

    # Build dataset
    return make_q_and_c_dataset(opt, tokenizer, queries, cand_ids, candidate_labels=labels, verbose=verbose)


def make_dev_set(opt, tokenizer, dev_data):
    """ Make labeled dataset for validation data. Include all candidates for evaluation.
    Args:
    - opt:
    - tokenizer:
    - dev_data: dict containing queries (list), candidates (list), and gold_hypernym_candidate_ids (list of lists, one per query).

    """

    # Load validation data
    queries = dev_data["queries"]
    gold_cand_ids = dev_data["gold_hypernym_candidate_ids"]
    nb_candidates = len(dev_data["candidates"])
    all_cand_ids = list(range(nb_candidates))
    cand_ids = []
    labels = []
    for i in range(len(queries)):
        y = [0] * nb_candidates
        for c in gold_cand_ids[i]:
            y[c] = 1
        cand_ids.append(all_cand_ids[:])
        labels.append(y)

    # Build dataset
    return make_q_and_c_dataset(opt, tokenizer, queries, cand_ids, candidate_labels=labels, verbose=False)


def sample_negative_examples(candidate_ids, pos_candidate_ids, per_query_nb_examples, verbose=False):
    """ Sample negative examples.

    Args:
    - candidate_ids: list of candidate IDs
    - pos_candidate_ids: list of lists of positive candidate IDs (one for each query)
    - per_query_nb_examples: sum of number of positive and negative examples per query. Note: if any queries have more than this number of positive examples, some will be discarded.
    
    """
    if verbose:
        logger.info("  Sampling negative examples with per_query_nb_examples={}".format(per_query_nb_examples))
    # Sample a bunch of indices at once to save time on generating random candidate indices
    buffer_size = 1000000
    sampled_indices = np.random.randint(len(candidate_ids), size=buffer_size)
    nb_queries = len(pos_candidate_ids)
    neg_candidate_ids = []
    buffer_index = 0
    for i in range(nb_queries):
        pos = pos_candidate_ids[i]
        pos_set = set(pos)
        nb_neg = max(0, per_query_nb_examples-len(pos))
        neg = []
        while len(neg) < nb_neg:
            sampled_index = sampled_indices[buffer_index]
            buffer_index += 1 
            if buffer_index == buffer_size:
                # Sample more indices
                sampled_indices = np.random.randint(len(candidate_ids), size=buffer_size)
                buffer_index = 0
            if candidate_ids[sampled_index] not in pos_set:
                neg.append(candidate_ids[sampled_index])
        neg_candidate_ids.append(neg)
    return neg_candidate_ids


def load_hypernyms(path):
    """Given the path of a hypernyms file, return list of lists of
    hypernyms.

    """
    with open(path) as f:
        hypernyms = []
        for line in f:
            h_list = line.strip().split("\t")
            hypernyms.append(h_list)
    return hypernyms


def get_missing_inputs(opt, token_ids, nb_tokens, lang_id):
    """ Given a tensor of padded token ids and a tensor indicating the number of actual (non padding tokens) per example, return dict containing additional inputs needed to feed the transformer.
    
    Args:
    opt
    tokenizer
    token_ids: tensor shape (n, max_length)
    nb_tokens: tensor shape (n, 1)
    lang_id: integer ID of the language of the examples

    """

    nb_examples, max_length = token_ids.size()
    inputs = {}
    
    # Segment IDs
    if opt.encoder_type == 'bert':
        inputs["token_type_ids"] = torch.tensor([[SEGMENT_ID] * max_length] * nb_examples, dtype=torch.long, requires_grad=False, device=opt.device)
    else:
        inputs["token_type_ids"] = None
        
    # Language IDs
    if opt.encoder_type == 'xlm':
        inputs["langs"] = torch.tensor([[lang_id] * max_length] * nb_examples, dtype=torch.long, requires_grad=False, device=opt.device)
    else:
        inputs["langs"] = None
        
    # Attention mask
    attention_mask = []
    for i in range(nb_examples):
        nb_tok = nb_tokens[i]
        padding_length = opt.max_seq_length - nb_tok
        mask = [1] * nb_tok + [0 if MASK_PADDING_WITH_ZERO else 1] * padding_length
        attention_mask.append(mask)
    inputs["attention_mask"] = torch.tensor(attention_mask, dtype=torch.long, requires_grad=False, device=opt.device)

    return inputs


def encode_string_inputs(opt, tokenizer, strings, verbose=False):
    """ Tokenize strings and return 2 tensors: input_ids (padded), nb_tokens (not including padding)

    """
    input_ids = []
    nb_tokens = []
    nb_processed = 0
    for string in strings:
        tokens = tokenizer.tokenize(string)
        token_ids = tokenizer.encode(tokens, add_special_tokens=True, max_length=opt.max_seq_length, pad_to_max_length=False)
        nb_tokens.append([len(token_ids)])
        # Pad
        padding_length = opt.max_seq_length - len(token_ids)
        token_ids += [PAD_TOKEN] * padding_length
        input_ids.append(token_ids)
        nb_processed += 1
        if verbose and nb_processed % 5000 == 0:
            logger.info("  Nb strings processed: {}".format(nb_processed))
    input_ids = torch.tensor(input_ids, dtype=torch.long, requires_grad=False, device=opt.device)
    nb_tokens = torch.tensor(nb_tokens, dtype=torch.long, requires_grad=False, device=opt.device)    
    return input_ids, nb_tokens


def make_q_or_c_dataset(opt, tokenizer, strings, verbose=False):
    """ Create an unlabeled dataset for inputs to an encoder (for queries or candidates). 

    """
    nb_strings = len(strings)
    if verbose:
        logger.info("***** Making dataset of string inputs (queries or candidates) ******")
        logger.info("  Nb strings: {}".format(nb_strings))
        logger.info("  Max length: {}".format(opt.max_seq_length))
    input_ids, nb_tokens = encode_string_inputs(opt, tokenizer, strings, verbose=verbose)
    return TensorDataset(input_ids, nb_tokens)


def make_q_and_c_dataset(opt, tokenizer, queries, candidate_ids, candidate_labels=None, verbose=False):
    """Create a dataset for query inputs and candidate ids, with optional labels.

    Args:
    - opt
    - tokenizer
    - queries: list of query strings
    - candidate_ids: list of lists containing the IDs of all the candidates to evaluate for a given query
    - candidate_labels: (optional) list of lists containing the labels of the candidate_ids (0 or 1)

    """

    # Check args
    assert len(queries) == len(candidate_ids)
    nb_candidates_fd = {}
    for c in candidate_ids:
        length = len(c)
        if length not in nb_candidates_fd:
            nb_candidates_fd[length] = 0
        nb_candidates_fd[length] += 1
    if len(nb_candidates_fd) > 1:
        msg = "Nb candidates must be same for all queries. "
        msg += "Found the following numbers: %s" % ", ".join(["{} (count={})".format(k,v) for (k,v) in nb_candidates_fd.items()])
        raise ValueError(msg)

    nb_queries = len(queries)
    nb_pos_examples = 0
    nb_neg_examples = 0
    if candidate_labels:
        for labels in candidate_labels:
            for label in labels:
                if label == 1:
                    nb_pos_examples += 1
                elif label == 0:
                    nb_neg_examples += 1
                else:
                    raise ValueError("unrecognized label '{}'".format(label))
    if verbose:
        logger.info("***** Making dataset ******")
        logger.info("  Nb queries: {}".format(nb_queries))
        if candidate_labels:
            logger.info("  Nb positive examples: {}".format(nb_pos_examples))
            logger.info("  Nb negative examples: {}".format(nb_neg_examples))
            logger.info("  Max length: {}".format(opt.max_seq_length))
    input_ids, nb_tokens = encode_string_inputs(opt, tokenizer, queries, verbose=verbose)
    # Log a few examples
    if verbose:
        for i in range(5):
            logger.info("*** Example ***")
            logger.info("  i: %d" % (i))
            logger.info("  query: %s" % queries[i])
            logger.info("  query token IDs: {}".format(input_ids[i]))
            logger.info("  nb tokens (without padding): {}".format(nb_tokens[i]))
            logger.info("  candidate ids: %s" % " ".join([str(x) for x in candidate_ids[i]]))
            logger.info("  candidate labels: %s" % " ".join([str(x) for x in candidate_labels[i]]))
    candidate_ids = torch.tensor(candidate_ids, dtype=torch.long, requires_grad=False, device=opt.device)
    candidate_labels = torch.tensor(candidate_labels, dtype=torch.float32, requires_grad=False, device=opt.device)
    return TensorDataset(input_ids, nb_tokens, candidate_ids, candidate_labels)


def load_hd_data(opt, set_type):
    """Load data from file. 
    Dataset can be a training, dev or test set for hypernym discovery,
    or a list of candidate hypernyms. 
    Return a dict containing: candidates and candidate2id, a list of queries (if the set type is not candidates) and a list of lists of gold hypernym candidate IDs (if the set type is train or dev).
    """
    
    if set_type not in ["train", "dev", "test", "candidates"]:
        raise ValueError("unrecognized set_type '{}'".format(set_type))

    # Load candidates, which we need regardless of the set type
    path_candidates = os.path.join(opt.data_dir, "candidates.txt")
    candidates = []
    with open(path_candidates) as f:
        for line in f:
            candidates.append(line.strip())
    data = {}
    data["candidates"] = candidates
    data["candidate2id"] = {x:i for (i,x) in enumerate(candidates)}
    if set_type == "candidates":
        return data

    # Load queries
    path_queries = os.path.join(opt.data_dir, "{}.queries.txt".format(set_type))
    queries = []
    with open(path_queries) as f:
        for line in f:
            queries.append(line.strip())
    data["queries"] = queries

    # Load gold_hypernym_candidate_ids (list of lists, one per
    # query, same order as source file)
    path_gold_hypernyms = os.path.join(opt.data_dir, '{}.gold.tsv'.format(set_type))            
    if (set_type in ["train", "dev"]) or (set_type=='test' and os.path.exists(path_gold_hypernyms)):
        gold_hypernyms = load_hypernyms(path_gold_hypernyms)
        gold_hypernym_candidate_ids = []
        for g_list in gold_hypernyms:
            g_id_list = []
            for g in g_list:
                if g in data["candidate2id"]:
                    g_id_list.append(data["candidate2id"][g])
                else:
                    raise KeyError("Gold hypernym '{}' not in candidate2id".format(g))
            gold_hypernym_candidate_ids.append(g_id_list)
        data["gold_hypernym_candidate_ids"] = gold_hypernym_candidate_ids        
    return data

            
def rotate_checkpoints(save_total_limit, output_dir, checkpoint_prefix, use_mtime=False, verbose=False):
    if not save_total_limit:
        return
    if save_total_limit <= 0:
        return

    # Check if we should delete older checkpoint(s)
    glob_checkpoints = glob.glob(os.path.join(output_dir, '{}-*'.format(checkpoint_prefix)))
    if len(glob_checkpoints) <= save_total_limit:
        return

    ordering_and_checkpoint_path = []
    for path in glob_checkpoints:
        if use_mtime:
            ordering_and_checkpoint_path.append((os.path.getmtime(path), path))
        else:
            regex_match = re.match('.*{}-([0-9]+)'.format(checkpoint_prefix), path)
            if regex_match and regex_match.groups():
                ordering_and_checkpoint_path.append((int(regex_match.groups()[0]), path))

    checkpoints_sorted = sorted(ordering_and_checkpoint_path)
    checkpoints_sorted = [checkpoint[1] for checkpoint in checkpoints_sorted]
    number_of_checkpoints_to_delete = max(0, len(checkpoints_sorted) - save_total_limit)
    checkpoints_to_be_deleted = checkpoints_sorted[:number_of_checkpoints_to_delete]
    for checkpoint in checkpoints_to_be_deleted:
        if verbose:
            logger.info("Deleting older checkpoint [{}] due to args.save_total_limit".format(checkpoint))
        shutil.rmtree(checkpoint)
