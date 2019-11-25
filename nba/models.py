from nba.process_data import DataManager
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from keras import layers, models, callbacks
from nba.common import data_path
import numpy as np
import random
import traceback


def run_naive_model():
    dm = DataManager()
    x, y = dm.get_labeled_data()
    x = x.reshape(x.shape[0], x.shape[1]*x.shape[2])
    x_train, x_val, y_train, y_val = train_test_split(x, y)
    rf = RandomForestClassifier()
    rf.fit(x_train, y_train)
    print(accuracy_score(y_val, rf.predict(x_val)))


def get_cnn_model(input_shape, filters, kernel_size, pool_size, dense_layers, dense_layers_width, convolutional_layers):
    input_layer = layers.Input(shape=input_shape)

    if convolutional_layers:
        x = layers.Conv1D(filters=filters, kernel_size=kernel_size, activation='relu')(input_layer)
        x = layers.MaxPooling1D(pool_size=pool_size)(x)

        for _ in range(1, convolutional_layers):
            x = layers.Conv1D(filters=filters, kernel_size=kernel_size, activation='relu')(x)
            x = layers.MaxPooling1D(pool_size=pool_size)(x)
        x = layers.Flatten()(x)

    if not convolutional_layers and dense_layers:
        x = layers.Dense(dense_layers_width)(input_layer)
    elif dense_layers:
        x = layers.Dense(dense_layers_width)(x)
    for _ in range(1, dense_layers):
        x = layers.Dense(dense_layers_width)(x)
    out = layers.Dense(1, activation='sigmoid')(x)

    model = models.Model(inputs=input_layer,  outputs=out)

    model.compile(optimizer='adam',
                  loss='binary_crossentropy',
                  metrics=['accuracy'])
    return model


def optimize_cnn():
    filters = [4, 8, 16 ,32, 64, 128, 256, 512, 1024]
    kernel_size = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    pool_size =[1, 2, 3, 4, 5, 6, 7, 8, 9]
    dense_layers = [0, 1, 2, 3]
    dense_layers_width = [32, 64, 128, 256]
    convolutional_layers = [0, 1, 2, 3]

    # filters = [128]
    # kernel_size = [3]
    # pool_size =[2]
    # dense_layers = [2]
    # dense_layers_width = [128]
    # convolutional_layers = [2]

    history_lengths = [8]
    transpose_history_data = [True, False]

    dms = dict()
    for i in history_lengths:
        for j in transpose_history_data:
            dm = DataManager(history_length=i, transpose_history_data=j)
            x, y = dm.get_labeled_data()
            x_train, x_val, y_train, y_val = train_test_split(x, y, random_state=1)
            x_val, x_test, y_val, y_test = train_test_split(x_val, y_val, train_size=.5, random_state=1)
            dms[(i, j)] = {'x_train':x_train,
                           'x_val':x_val,
                           'x_test':x_test,
                           'y_train':y_train,
                           'y_val':y_val,
                           'y_test':y_test}


    results = list()

    for i in range(24):
        try:
            filters_choice = random.choice(filters)
            kernel_size_choice = random.choice(kernel_size)
            pool_size_choice = random.choice(pool_size)
            dense_layers_choice = random.choice(dense_layers)
            dense_layers_width_choice = random.choice(dense_layers_width)
            convolutional_layers_choice = random.choice(convolutional_layers)
            history_lengths_choice = random.choice(history_lengths)
            transpose_history_data_choice = random.choice(transpose_history_data)

            model = get_cnn_model(input_shape = (dms[(history_lengths_choice, transpose_history_data_choice)]['x_train'].shape[1], dms[(history_lengths_choice, transpose_history_data_choice)]['x_train'].shape[2]),
                                  filters=filters_choice,
                                  kernel_size=kernel_size_choice,
                                  pool_size=pool_size_choice,
                                  dense_layers=dense_layers_choice,
                                  dense_layers_width=dense_layers_width_choice,
                                  convolutional_layers=convolutional_layers_choice
                                  )
            cb = callbacks.EarlyStopping(monitor='val_loss',
                                               min_delta=0,
                                               patience=10,
                                               verbose=0, mode='auto')
            mcp_save = callbacks.ModelCheckpoint('{}/test.h5'.format(data_path), save_best_only=True, monitor='val_loss',
                                                       verbose=1)
            model.fit(dms[(history_lengths_choice, transpose_history_data_choice)]['x_train'],
                      dms[(history_lengths_choice, transpose_history_data_choice)]['y_train'],
                      validation_data= (dms[(history_lengths_choice, transpose_history_data_choice)]['x_val'],
                                        dms[(history_lengths_choice, transpose_history_data_choice)]['y_val']),
                                callbacks=[cb, mcp_save], epochs=200, batch_size=64)

            model = models.load_model('{}/test.h5'.format(data_path))
            preds = np.rint(model.predict(dms[(history_lengths_choice, transpose_history_data_choice)]['x_test']))
            score = accuracy_score(dms[(history_lengths_choice, transpose_history_data_choice)]['y_test'], preds.astype(int))

            results.append({'filters':filters_choice,
                                  'kernel_size':kernel_size_choice,
                                  'pool_size':pool_size_choice,
                                  'dense_layers':dense_layers_choice,
                                  'dense_layers_width':dense_layers_width_choice,
                                  'convolutional_layers':convolutional_layers_choice,
                                    'accuracy':score,
                                    'history_length':history_lengths_choice,
                                    'transpose_history_data':transpose_history_data_choice
                                    })
            results = sorted(results, key = lambda x: x['accuracy'])
            print(results)
        except:
            traceback.print_exc()


def build_datasets(history_length_choices, transpose_history_data_choices):
    dm1 = DataManager(history_length=history_length_choices[0], transpose_history_data=transpose_history_data_choices[0])
    dm1.load_raw_data()
    dm1.assign_home_for_teams()
    # dm1.calculate_team_game_rating(0)
    # dm1.calculate_team_game_rating(1)
    # dm1.calculate_team_game_rating(2)
    # dm1.calculate_team_game_rating(3)

    for history_length in history_length_choices:
        for transpose_history_data in transpose_history_data_choices:
            dm = DataManager(history_length = history_length, transpose_history_data = transpose_history_data)

            dm.load_processed_data()
            dm.initial_team_data_columns = dm1.initial_team_data_columns.copy()
            dm.build_past_n_game_dataset()
            dm.combine_past_n_game_datasets()
            del dm

if __name__ == '__main__':
    # history_length_choices = [8]
    # transpose_history_data_choices = [False]
    # build_datasets(history_length_choices, transpose_history_data_choices)
    optimize_cnn()

