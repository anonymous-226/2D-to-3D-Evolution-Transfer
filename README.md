# 2D-to-3D-Evolution-Transfer
## Visualizations
- Animated
![123](https://user-images.githubusercontent.com/111099396/185350189-9d104925-39fb-4bc1-b562-baafc3ec9378.gif)
- Texture Evolution Comparision
![tex](https://user-images.githubusercontent.com/111099396/185362242-8019be1f-65b4-417b-88b3-35427fee5955.png)
- Shape Evolution Comparision
![shape](https://user-images.githubusercontent.com/111099396/185362269-c5f7325a-c63d-4a5f-8751-c4614bf4ef4e.png)
- Evolutionary shapes using the parameter Alpha
![alpha](https://user-images.githubusercontent.com/111099396/185362286-8a901859-0d33-42ed-bb70-2bb8577a0007.png)


## Prerequisites
- Download code & pre-trained model:
Git clone the code by:
```
git clone https://github.com/anonymous-226/2D-to-3D-Evolution-Transfer $ROOTPATH
```
The pretrained model can be found from [here](https://drive.google.com/file/d/1Agf_G9OaCvXPoenRK5vpj3VckuFPGRMg/view?usp=sharing), which should be unzipped in `$ROOTPATH`.
- Install packages:
```
conda create -n evo_trans python=3.6
conda activate evo_trans
conda install pytorch==1.8.0 torchvision==0.9.0 cudatoolkit=11.1 -c pytorch -c conda-forge
pip install -r requirements.txt
```

- Install external tools:

```
cd $ROOTPATH/2D-to-3D-Evolution-Transfer/dependency
unzip meshzoo-0.4.3.zip
cd meshzoo-0.4.3
python setup.py install
```

```
cd $ROOTPATH/2D-to-3D-Evolution-Transfer/dependency
unzip neural_renderer.zip
cd neural_renderer
python setup.py install
```

## Run the test
Run the following command from the `$ROOTPATH/2D-to-3D-Evolution-Transfer` directory:
```
python -m evo_trans.experiments.test_df2
```
The result can be found in `$ROOTPATH/2D-to-3D-Evolution-Transfer/evo_trans/cachedir/visualization` directory.
