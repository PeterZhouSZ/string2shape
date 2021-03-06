'''Sequence to sequence example in Keras (character-level).
This script demonstrates how to implement a basic character-level
sequence-to-sequence model. We apply it to translating
short English sentences into short French sentences,
character-by-character. Note that it is fairly unusual to
do character-level machine translation, as word-level
models are more common in this domain.

# Summary of the algorithm

- We start with input sequences from a domain (e.g. English sentences)
    and correspding target sequences from another domain
    (e.g. French sentences).

- An encoder LSTM turns input sequences to 2 state vectors
    (we keep the last LSTM state and discard the outputs).

- A decoder LSTM is trained to turn the target sequences into
    the same sequence but offset by one timestep in the future,
    a training process called "teacher forcing" in this context.
    Is uses as initial state the state vectors from the encoder.
    Effectively, the decoder learns to generate `targets[t+1...]`
    given `targets[...t]`, conditioned on the input sequence.

- In inference mode, when we want to decode unknown input sequences, we:
    - Encode the input sequence into state vectors
    - Start with a target sequence of size 1
        (just the start-of-sequence character)
    - Feed the state vectors and 1-char target sequence
        to the decoder to produce predictions for the next character
    - Sample the next character using these predictions
        (we simply use argmax).
    - Append the sampled character to the target sequence
    - Repeat until we generate the end-of-sequence character or we
        hit the character limit.

# Data download

English to French sentence pairs.
http://www.manythings.org/anki/fra-eng.zip

Lots of neat sentence pairs datasets can be found at:
http://www.manythings.org/anki/

# References

- Sequence to Sequence Learning with Neural Networks
    https://arxiv.org/abs/1409.3215

- Learning Phrase Representations using
    RNN Encoder-Decoder for Statistical Machine Translation
    https://arxiv.org/abs/1406.1078

'''

from __future__ import print_function
import argparse
import os
import numpy as np

from neuralnets.seq2seq import Seq2SeqAE, Seq2SeqRNN, Seq2SeqNoMaskRNN, Seq2SeqDeepRNN
from neuralnets.grammar import TilingGrammar
from neuralnets.utils import load_categories_dataset, decode_smiles_from_indexes, from_one_hot_array
from neuralnets.shape_graph import smiles_variations

from keras.callbacks import ModelCheckpoint, ReduceLROnPlateau, Callback
from keras.utils import plot_model

import matplotlib.pyplot as plt
from matplotlib import rcParams
rcParams['font.family'] = 'sans-serif'
rcParams['font.sans-serif'] = ['Verdana']

from collections import Counter

def most_common_elem(lst):
    data = Counter(lst)
    return data.most_common(1)[0][0]

class PlotLearning(Callback):
 
    def set_filename(self, name='filename'):
        self.filename = name

    def on_train_begin(self, logs={}):
        self.i = 0
        self.x = []
        self.losses = []
        self.val_losses = []
        self.acc = []
        self.val_acc = []
        self.fig = plt.figure()
        
        self.logs = []

    def on_epoch_end(self, epoch, logs={}):
        
        self.logs.append(logs)
        self.x.append(self.i)
        self.losses.append(logs.get('loss'))
        self.val_losses.append(logs.get('val_loss'))
        self.acc.append(logs.get('acc'))
        self.val_acc.append(logs.get('val_acc'))
        self.i += 1
        f, (ax1, ax2) = plt.subplots(1, 2, sharex=True)

        ax1.plot(self.x, self.losses, label="loss")
        ax1.plot(self.x, self.val_losses, label="val_loss")
        ax1.legend()
        
        ax2.plot(self.x, self.acc, label="accuracy")
        ax2.plot(self.x, self.val_acc, label="validation accuracy")
        ax2.legend()
        
        plt.savefig(self.filename + '_loss_history.pdf', bbox_inches='tight')
        plt.close()


NUM_EPOCHS = 1
BATCH_SIZE = 200
LSTM_SIZE = 512
WORD_LENGTH = 120
MODEL = 'rnn'

def get_arguments():
    parser = argparse.ArgumentParser(description='Sequence to sequence autoencoder network')
    parser.add_argument('data', type=str, help='The HDF5 file containing preprocessed data.')
    parser.add_argument('out', type=str,
                        help='Where to save the trained model. If this file exists, it will be opened and resumed.')
    parser.add_argument('grammar', type=str, help='The HDF5 file with the tiling grammar.')
    parser.add_argument('--model', type=str, default=MODEL,
                        help='What model to train: autoencoder, rnn, deep_rnn, no_mask_rnn.')
    parser.add_argument('--epochs', type=int, metavar='N', default=NUM_EPOCHS,
                        help='Number of epochs to run during training.')
    parser.add_argument('--word_length', type=int, metavar='N', default=WORD_LENGTH,
                        help='Length of input sequences')
    parser.add_argument('--batch_size', type=int, metavar='N', default=BATCH_SIZE,
                        help='Number of samples to process per minibatch during training.')
    return parser.parse_args()

def decode_sequence_ae(model,
                    input_seq,
                    input_mask,
                    input_len,
                    output_charset,
                    bounds=None,
                    max_length=WORD_LENGTH):
    num_decoder_tokens = len(output_charset)
    max_category = max(output_charset)

    # Encode the input as state vectors.
    #states_value = model.encoder.predict(input_seq)
    states_value = model.encoder.predict([input_seq, input_mask])#mask

    # Generate empty target sequence of length 1.
    target_seq = np.zeros((1, 1, num_decoder_tokens))
    target_mask = np.zeros((1, 1, num_decoder_tokens))#mask

    # Populate the first character of target sequence with the start character.
    #target_seq[0, 0, max_category] = 1.
    target_min_bound = np.full(input_len, 0, dtype=int)
    target_max_bound = np.full(input_len, -1, dtype=int)

    if bounds != None:
        target_min_bound = np.array([pair[0] for pair in bounds]) 
        target_max_bound = np.array([pair[1] for pair in bounds]) 

    #print('input mask', input_mask)
    # Sampling loop for a batch of sequences
    # (to simplify, here we assume a batch of size 1).
    stop_condition = False
    decoded_sequence = []
    while not stop_condition:
        #Update the target mask
        char_id = len(decoded_sequence)
        target_mask[0][0] = input_mask[0][char_id]
        #print('target mask', target_mask[0][0])

        output_tokens, h, c = model.decoder.predict([target_seq, target_mask] + states_value)

        min_bound = target_min_bound[char_id]
        max_bound = target_max_bound[char_id]        
        # if bounds != None:
        #     min_bound = max_category - target_max_bound[char_id] + 1
        #     max_bound = max_category - target_min_bound[char_id] + 1

        # Sample a token
        sampled_token_index = num_decoder_tokens - 1
        if min_bound < max_bound:
            sampled_token_index = min_bound + np.argmax(output_tokens[0, -1, min_bound:max_bound])
            sampled_category = output_charset[sampled_token_index]
            decoded_sequence.append(sampled_category)
        elif min_bound == 0 and max_bound == -1:
            sampled_token_index = np.argmax(output_tokens[0, -1, :])
            sampled_category = output_charset[sampled_token_index]
            decoded_sequence.append(sampled_category)
        else:
            decoded_sequence.append(max_category)

        # Exit condition: either hit max length
        # or find stop character.
        if len(decoded_sequence) >= input_len:
            stop_condition = True

        # Update the target sequence (of length 1).
        target_seq = np.zeros((1, 1, num_decoder_tokens))
        target_seq[0, 0, sampled_token_index] = 1.

        # Update states
        states_value = [h, c]

    return decoded_sequence

def predict_sequence(model,
                    input_seq,
                    input_mask=None):
    if input_mask is None:
        return model.rnn.predict(input_seq)
    else:
        return model.rnn.predict([input_seq, input_mask])

def decode_sequence_rnn(model,
                    input_seq,                    
                    input_len,
                    output_charset,
                    input_mask=None):

    output_sequence = predict_sequence(model, input_seq, input_mask)

    decoded_sequence = []
    while len(decoded_sequence) < input_len:
        char_id = len(decoded_sequence)
        sampled_token_index = np.argmax(output_sequence[0, char_id, :])
        sampled_category = output_charset[sampled_token_index]
        decoded_sequence.append(sampled_category)

    return decoded_sequence

def decode_sequence(model,
                    grammar,
                    input_charset,
                    input_word,
                    max_length=WORD_LENGTH,
                    num_variants=10):

    if num_variants <= 1:
        num_variants = 1 
    ##############################################################################################################
    #Generate multiple string variants for the input graph
    ##############################################################################################################
    padded_node_ids = []
    num_nodes = 0 
    for char_id, _ in enumerate(input_word):
        if input_word[char_id] in grammar.charset:
            padded_node_ids.append(num_nodes)
            num_nodes += 1
        else:
            padded_node_ids.append(max_length)

    dummy_node_id = num_nodes

    for i, _ in enumerate(padded_node_ids):
        if padded_node_ids[i] == max_length:
            padded_node_ids[i] = dummy_node_id

    padded_node_ids.append(dummy_node_id) #ensure at least one occurrence

    smiles_variants, node_variants = smiles_variations(input_word, padded_node_ids, grammar, num_variants - 1)

    smiles_strings = [input_word] + smiles_variants
    node_lists = [padded_node_ids] + node_variants
    edge_lists = []
    for word, nodes in zip(smiles_strings, node_lists):
        edge_lists.append(grammar.smiles_to_edges(word, nodes))


    input_sequences = np.empty(dtype='float32', shape=(num_variants, max_length, len(input_charset)))
    input_masks = np.empty(dtype='float32', shape=(num_variants, max_length, grammar.categories_prefix[-1] + 1))
    for i, word in enumerate(smiles_strings):
        input_sequences[i] = grammar.smiles_to_one_hot(word.ljust(max_length), input_charset)
        input_masks[i] = grammar.smiles_to_mask(word, max_length)

    ##############################################################################################################
    #Classify each string (estimate edge configurations)
    ##############################################################################################################
    output_charset = list(range(0, grammar.categories_prefix[-1] + 1, 1))

    decoded_sequences = []
    for i in range(num_variants):
        decoded_sequences.append(decode_sequence_rnn(model, input_sequences[i:i+1], len(smiles_strings[i]), output_charset, input_masks[i:i+1]))

    output_sequence = []
    per_edge_categories = []
    for edge_id, edge in enumerate(edge_lists[0]):
        local_categories = [decoded_sequences[0][edge_id]]
        if edge[0] != dummy_node_id or edge[1] != dummy_node_id:
            for j in range(1, num_variants):
                if edge in edge_lists[j]: #edge direction can be reversed in the other list
                    idx = edge_lists[j].index(edge)
                    local_categories.append(decoded_sequences[j][idx])
        per_edge_categories.append(local_categories)
        output_sequence.append(most_common_elem(local_categories))

    return output_sequence

def main():
    args = get_arguments()

    tile_grammar = TilingGrammar([])
    tile_grammar.load(args.grammar)

    data_train, categories_train, masks_train, data_test, categories_test, masks_test, charset, charset_cats = load_categories_dataset(args.data)

    num_encoder_tokens = len(charset)
    num_decoder_tokens = len(charset_cats)
    #max_category = max(charset_cats)

    if categories_train.shape != masks_train.shape or data_train.shape[0] != categories_train.shape[0] or data_train.shape[1] != categories_train.shape[1]:
        print('Incompatible input array dimensions')
        print('Sample categories shape: ', categories_train.shape)
        print('Sample masks shape: ', masks_train.shape)
        print('Sample data shape: ', data_train.shape)

    print('Number of unique input tokens: ', num_encoder_tokens)
    print('Number of unique output tokens: ', num_decoder_tokens)

    encoder_input_data = data_train.astype(dtype='float32')
    decoder_input_masks = masks_train.astype(dtype='float32')
    decoder_input_data = categories_train.astype(dtype='float32')

    encoder_test_data = data_test.astype(dtype='float32')
    decoder_test_masks = masks_test.astype(dtype='float32')

    ##############################################################################################################
    #Sequence to sequence autoencoder
    ##############################################################################################################
    if args.model == 'autoencoder':
        decoder_target_data = np.zeros(categories_train.shape, dtype='float32')
        for w_id in range(decoder_input_data.shape[0]):
            for c_id in range(decoder_input_data.shape[1]):
                for one_h_id_c in range(decoder_input_data.shape[2]):
                        if c_id > 0:
                            # decoder_target_data will be ahead by one timestep
                            # and will not include the start character.
                            decoder_target_data[w_id][c_id-1][one_h_id_c] = 1.

        model = Seq2SeqAE()
        if os.path.isfile(args.out):
            model.load(charset, charset_cats, args.out, lstm_size=LSTM_SIZE)
        else:
            model.create(charset, charset_cats, lstm_size=LSTM_SIZE)

        if args.epochs > 0:
            checkpointer = ModelCheckpoint(filepath=args.out,
                                        verbose=1,
                                        save_best_only=True)

            reduce_lr = ReduceLROnPlateau(monitor = 'val_loss',
                                            factor = 0.2,
                                            patience = 3,
                                            min_lr = 0.000001)

            filename, ext = os.path.splitext(args.out)
            plot_model(model.autoencoder, to_file=filename + '_autoencoder_nn.pdf', show_shapes=True)
            plot_model(model.decoder, to_file=filename + '_decoder_nn.pdf', show_shapes=True)

            plot = PlotLearning()
            plot.set_filename(filename)

            history = model.autoencoder.fit([encoder_input_data, decoder_input_data, decoder_input_masks], decoder_target_data,
                                batch_size=args.batch_size,
                                epochs=args.epochs,
                                validation_split=0.2,
                                callbacks=[checkpointer, reduce_lr, plot])

            # Save model
            model.autoencoder.save(args.out)

        #test-decode a couple of train examples
        sample_ids = np.random.randint(0, len(data_train), 4)
        for word_id in sample_ids:
            print ('===============================')
            train_string = decode_smiles_from_indexes(map(from_one_hot_array, data_train[word_id]), charset)
            print ('train string: ', train_string)

            train_sequence = []
            for char_id in range(categories_train[word_id].shape[0]):
                token_index = np.argmax(categories_train[word_id][char_id, :])
                train_category = charset_cats[token_index]
                train_sequence.append(train_category)

            input_seq = encoder_input_data[word_id: word_id + 1]
            input_mask = decoder_input_masks[word_id: word_id + 1]
            category_bounds = tile_grammar.smiles_to_categories_bounds(train_string)
            decoded_seq_1 = decode_sequence_ae(model, input_seq, input_mask, len(train_string), charset_cats, category_bounds)
            #print ('decoded categories (w/ bounds):', decoded_seq_1)

            decoded_seq_2 = decode_sequence_ae(model, input_seq, input_mask, len(train_string), charset_cats)
            #print ('decoded categories (no bounds):', decoded_seq_2)

            print ('[train, decoded, decoded] categories :', zip(train_sequence[:len(train_string)], decoded_seq_1, decoded_seq_2))
            # print ('categories bounds:', tile_grammar.smiles_to_categories_bounds(train_string))


        #test-decode a couple of test examples
        sample_ids = np.random.randint(0, len(data_test), 8)
        for word_id in sample_ids:
            print ('===============================')
            test_string = decode_smiles_from_indexes(map(from_one_hot_array, data_test[word_id]), charset)
            print ('test string: ', test_string)

            test_sequence = []
            for char_id in range(categories_test[word_id].shape[0]):
                token_index = np.argmax(categories_test[word_id][char_id, :])
                test_category = charset_cats[token_index]
                test_sequence.append(test_category)
            #print ('test categories               :', test_sequence[:len(test_string)])

            input_seq = encoder_test_data[word_id: word_id + 1]
            input_mask = decoder_test_masks[word_id: word_id + 1]
            category_bounds = tile_grammar.smiles_to_categories_bounds(test_string)
            decoded_seq_1 = decode_sequence_ae(model, input_seq, input_mask, len(test_string), charset_cats, category_bounds)
            #print ('decoded categories (w/ bounds):', decoded_seq_1)

            decoded_seq_2 = decode_sequence_ae(model, input_seq, input_mask, len(test_string), charset_cats)
            #print ('decoded categories (no bounds):', decoded_seq_2)
            
            print ('[train, decoded, decoded] categories :', zip(test_sequence[:len(test_string)], decoded_seq_1, decoded_seq_2))
            # print ('categories bounds:', tile_grammar.smiles_to_categories_bounds(test_string))

    ##############################################################################################################
    #Simple (deep) RNN
    ##############################################################################################################
    elif args.model == 'rnn':

        model = Seq2SeqRNN()
        if os.path.isfile(args.out):
            model.load(charset, charset_cats, args.out, lstm_size=LSTM_SIZE)
        else:
            model.create(charset, charset_cats, lstm_size=LSTM_SIZE)

        if args.epochs > 0:
            checkpointer = ModelCheckpoint(filepath=args.out,
                                        verbose=1,
                                        save_best_only=True)

            reduce_lr = ReduceLROnPlateau(monitor = 'val_loss',
                                            factor = 0.2,
                                            patience = 3,
                                            min_lr = 0.000001)

            filename, ext = os.path.splitext(args.out)
            plot_model(model.rnn, to_file=filename + '_rnn.pdf', show_shapes=True)
            plot = PlotLearning()
            plot.set_filename(filename)

            history = model.rnn.fit([encoder_input_data, decoder_input_masks], decoder_input_data,
                                batch_size=args.batch_size,
                                epochs=args.epochs,
                                validation_split=0.2,
                                callbacks=[checkpointer, reduce_lr, plot])

            # Save model
            model.rnn.save(args.out)

        #test-decode a couple of train examples
        sample_ids = np.random.randint(0, len(data_train), 2)
        for word_id in sample_ids:
            print ('===============================')
            train_string = decode_smiles_from_indexes(map(from_one_hot_array, data_train[word_id]), charset)
            print ('train string: ', train_string)

            train_sequence = []
            for char_id in range(categories_train[word_id].shape[0]):
                token_index = np.argmax(categories_train[word_id][char_id, :])
                train_category = charset_cats[token_index]
                train_sequence.append(train_category)

            input_seq = encoder_input_data[word_id: word_id + 1]
            input_mask = decoder_input_masks[word_id: word_id + 1]
            decoded_seq_1 = decode_sequence_rnn(model, input_seq, len(train_string), charset_cats, input_mask)

            print ('(train, decoded) categories :', zip(train_sequence, decoded_seq_1))


        #test-decode a couple of test examples
        sample_ids = np.random.randint(0, len(data_test), 2)
        for word_id in sample_ids:
            print ('===============================')
            test_string = decode_smiles_from_indexes(map(from_one_hot_array, data_test[word_id]), charset)
            print ('test string: ', test_string)

            test_sequence = []
            for char_id in range(categories_test[word_id].shape[0]):
                token_index = np.argmax(categories_test[word_id][char_id, :])
                test_category = charset_cats[token_index]
                test_sequence.append(test_category)

            input_seq = encoder_test_data[word_id: word_id + 1]
            input_mask = decoder_test_masks[word_id: word_id + 1]
            decoded_seq_1 = decode_sequence_rnn(model, input_seq, len(test_string), charset_cats, input_mask)
            
            print ('(test, decoded) categories :', zip(test_sequence, decoded_seq_1))

            num_smiles_variants = 32
            decoded_seq_2 = decode_sequence(model, tile_grammar, charset, test_string, max_length=args.word_length, num_variants=num_smiles_variants)
            print ('(test, decoded_1, decoded_' + str(num_smiles_variants) + ') categories :', zip(test_sequence, decoded_seq_1, decoded_seq_2))


    ###############################################################################################################
    #Deep RNN
    ###############################################################################################################
    elif args.model == 'deep_rnn':

        model = Seq2SeqDeepRNN()
        if os.path.isfile(args.out):
            model.load(charset, charset_cats, args.out, lstm_size=LSTM_SIZE)
        else:
            model.create(charset, charset_cats, lstm_size=LSTM_SIZE)

        if args.epochs > 0:
            checkpointer = ModelCheckpoint(filepath=args.out,
                                        verbose=1,
                                        save_best_only=True)

            reduce_lr = ReduceLROnPlateau(monitor = 'val_loss',
                                            factor = 0.2,
                                            patience = 3,
                                            min_lr = 0.000001)

            filename, ext = os.path.splitext(args.out)
            plot_model(model.rnn, to_file=filename + '_rnn.pdf', show_shapes=True)
            plot = PlotLearning()
            plot.set_filename(filename)

            history = model.rnn.fit([encoder_input_data, decoder_input_masks], decoder_input_data,
                                batch_size=args.batch_size,
                                epochs=args.epochs,
                                validation_split=0.2,
                                callbacks=[checkpointer, reduce_lr, plot])

            # Save model
            model.rnn.save(args.out)

        #test-decode a couple of train examples
        sample_ids = np.random.randint(0, len(data_train), 2)
        for word_id in sample_ids:
            print ('===============================')
            train_string = decode_smiles_from_indexes(map(from_one_hot_array, data_train[word_id]), charset)
            print ('train string: ', train_string)

            train_sequence = []
            for char_id in range(categories_train[word_id].shape[0]):
                token_index = np.argmax(categories_train[word_id][char_id, :])
                train_category = charset_cats[token_index]
                train_sequence.append(train_category)

            input_seq = encoder_input_data[word_id: word_id + 1]
            input_mask = decoder_input_masks[word_id: word_id + 1]
            decoded_seq_1 = decode_sequence_rnn(model, input_seq, len(train_string), charset_cats, input_mask)

            print ('(train, decoded) categories :', zip(train_sequence, decoded_seq_1))


        #test-decode a couple of test examples
        sample_ids = np.random.randint(0, len(data_test), 2)
        for word_id in sample_ids:
            print ('===============================')
            test_string = decode_smiles_from_indexes(map(from_one_hot_array, data_test[word_id]), charset)
            print ('test string: ', test_string)

            test_sequence = []
            for char_id in range(categories_test[word_id].shape[0]):
                token_index = np.argmax(categories_test[word_id][char_id, :])
                test_category = charset_cats[token_index]
                test_sequence.append(test_category)

            input_seq = encoder_test_data[word_id: word_id + 1]
            input_mask = decoder_test_masks[word_id: word_id + 1]
            decoded_seq_1 = decode_sequence_rnn(model, input_seq, len(test_string), charset_cats, input_mask)
            
            print ('(test, decoded) categories :', zip(test_sequence, decoded_seq_1))

            num_smiles_variants = 32
            decoded_seq_2 = decode_sequence(model, tile_grammar, charset, test_string, max_length=args.word_length, num_variants=num_smiles_variants)
            print ('(test, decoded_1, decoded_' + str(num_smiles_variants) + ') categories :', zip(test_sequence, decoded_seq_1, decoded_seq_2))

    ###############################################################################################################
    #Simple RNN without masking
    ###############################################################################################################
    elif args.model == 'no_mask_rnn':

        model = Seq2SeqNoMaskRNN()
        if os.path.isfile(args.out):
            model.load(charset, charset_cats, args.out, lstm_size=LSTM_SIZE)
        else:
            model.create(charset, charset_cats, lstm_size=LSTM_SIZE)

        if args.epochs > 0:
            checkpointer = ModelCheckpoint(filepath=args.out,
                                        verbose=1,
                                        save_best_only=True)

            reduce_lr = ReduceLROnPlateau(monitor = 'val_loss',
                                            factor = 0.2,
                                            patience = 3,
                                            min_lr = 0.000001)

            filename, ext = os.path.splitext(args.out)
            plot_model(model.rnn, to_file=filename + '_rnn.pdf', show_shapes=True)
            plot = PlotLearning()
            plot.set_filename(filename)

            history = model.rnn.fit(encoder_input_data, decoder_input_data,
                                batch_size=args.batch_size,
                                epochs=args.epochs,
                                validation_split=0.2,
                                callbacks=[checkpointer, reduce_lr, plot])

            # Save model
            model.rnn.save(args.out)

        #test-decode a couple of train examples
        sample_ids = np.random.randint(0, len(data_train), 2)
        for word_id in sample_ids:
            print ('===============================')
            train_string = decode_smiles_from_indexes(map(from_one_hot_array, data_train[word_id]), charset)
            print ('train string: ', train_string)

            train_sequence = []
            for char_id in range(categories_train[word_id].shape[0]):
                token_index = np.argmax(categories_train[word_id][char_id, :])
                train_category = charset_cats[token_index]
                train_sequence.append(train_category)

            input_seq = encoder_input_data[word_id: word_id + 1]
            input_mask = decoder_input_masks[word_id: word_id + 1]
            decoded_seq_1 = decode_sequence_rnn(model, input_seq, len(train_string), charset_cats)

            print ('(train, decoded) categories :', zip(train_sequence, decoded_seq_1))


        #test-decode a couple of test examples
        sample_ids = np.random.randint(0, len(data_test), 2)
        for word_id in sample_ids:
            print ('===============================')
            test_string = decode_smiles_from_indexes(map(from_one_hot_array, data_test[word_id]), charset)
            print ('test string: ', test_string)

            test_sequence = []
            for char_id in range(categories_test[word_id].shape[0]):
                token_index = np.argmax(categories_test[word_id][char_id, :])
                test_category = charset_cats[token_index]
                test_sequence.append(test_category)

            input_seq = encoder_test_data[word_id: word_id + 1]
            input_mask = decoder_test_masks[word_id: word_id + 1]
            decoded_seq_1 = decode_sequence_rnn(model, input_seq, len(test_string), charset_cats)
            
            print ('(train, decoded) categories :', zip(test_sequence, decoded_seq_1))



if __name__ == '__main__':
    main()
